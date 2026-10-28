# -*- coding: utf-8 -*-
"""Motor do Drive Clone (sem interface).

Mapeia, baixa e envia pastas do Google Drive mantendo a hierarquia.
Quem chama passa um Ctx com log, progresso e um Event para cancelar.
"""

import csv
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPE_LEITURA = "https://www.googleapis.com/auth/drive.readonly"
SCOPE_ARQUIVOS = "https://www.googleapis.com/auth/drive.file"
SCOPE_TOTAL = "https://www.googleapis.com/auth/drive"

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
GAPPS = "application/vnd.google-apps."
OFFICE = "application/vnd.openxmlformats-officedocument."

# arquivos nativos do Google viram Office na hora de baixar
EXPORT = {
    GAPPS + "document": (OFFICE + "wordprocessingml.document", ".docx"),
    GAPPS + "spreadsheet": (OFFICE + "spreadsheetml.sheet", ".xlsx"),
    GAPPS + "presentation": (OFFICE + "presentationml.presentation", ".pptx"),
    GAPPS + "drawing": ("application/pdf", ".pdf"),
}

# usado no envio com "converter": volta a ser arquivo nativo do Google
CONVERTER = {
    ".docx": GAPPS + "document",
    ".xlsx": GAPPS + "spreadsheet",
    ".pptx": GAPPS + "presentation",
}

CHUNK = 16 * 1024 * 1024
IGNORAR = {"_manifesto.csv", "_para_baixar_manualmente.txt", ".estado_envio.json"}
SEGURO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
              *(f"LPT{i}" for i in range(1, 10))}


class Cancelado(Exception):
    pass


class Ctx:
    """Canal entre o motor e quem chama (janela, testes)."""

    def __init__(self, log=print, progresso=None, cancelar=None):
        self.log = log
        self.progresso = progresso or (lambda feito, total: None)
        self.cancelar = cancelar or threading.Event()

    def checar(self):
        if self.cancelar.is_set():
            raise Cancelado()


class No:
    """Pasta ou arquivo da árvore (do Drive ou do disco)."""

    def __init__(self, id, nome, mime, tamanho=None, md5=None):
        self.id = id
        self.nome = nome
        self.mime = mime
        self.tamanho = tamanho
        self.md5 = md5
        self.filhos = []

    @property
    def pasta(self):
        return self.mime == FOLDER

    def percorrer(self):
        yield self
        for f in self.filhos:
            yield from f.percorrer()


# ---------- configuração e arquivos locais ----------

def pasta_dados():
    base = os.environ.get("DRIVECLONE_HOME")
    if base:
        p = Path(base)
    elif os.name == "nt":
        p = Path(os.environ.get("APPDATA") or Path.home()) / "DriveClone"
    else:
        p = Path.home() / ".driveclone"
    p.mkdir(parents=True, exist_ok=True)
    return p


def caminho_credenciais():
    return pasta_dados() / "credentials.json"


def caminho_token(quem):
    return pasta_dados() / f"token_{quem}.json"


def carregar_config():
    try:
        return json.loads((pasta_dados() / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def salvar_config(dados):
    try:
        (pasta_dados() / "config.json").write_text(
            json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def gravar_privado(caminho, texto):
    """Grava arquivo com segredo; no Linux/macOS só o dono lê."""
    caminho = Path(caminho)
    caminho.write_text(texto, encoding="utf-8")
    try:
        os.chmod(caminho, 0o600)
    except OSError:
        pass


def validar_credenciais(caminho):
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except Exception:
        return False, "O arquivo não é um JSON válido."
    if "installed" in dados:
        return True, ""
    if "web" in dados:
        return False, ("Esse cliente é do tipo 'Aplicativo da Web'. "
                       "Crie outro do tipo 'App para computador'.")
    return False, "Formato não reconhecido. Baixe o JSON do ID do cliente OAuth."


def procurar_credenciais(pastas):
    """Acha um credentials.json / client_secret*.json válido nas pastas dadas."""
    for pasta in pastas:
        pasta = Path(pasta)
        if not pasta.is_dir():
            continue
        for c in [pasta / "credentials.json", *sorted(pasta.glob("client_secret*.json"))]:
            if c.is_file() and validar_credenciais(c)[0]:
                return c
    return None


def importar_credenciais(origem):
    ok, msg = validar_credenciais(origem)
    if not ok:
        raise ValueError(msg)
    destino = caminho_credenciais()
    gravar_privado(destino, Path(origem).read_text(encoding="utf-8"))
    return destino


# ---------- utilidades ----------

ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]+$")


def extrair_id(valor):
    valor = (valor or "").strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", valor) or \
        re.search(r"[?&]id=([A-Za-z0-9_-]+)", valor)
    achado = m.group(1) if m else valor
    if not ID_VALIDO.match(achado):
        raise ValueError("Link ou ID de pasta inválido.")
    return achado


def executar(req, tentativas=6):
    for i in range(tentativas):
        try:
            return req.execute(num_retries=2)
        except HttpError as e:
            transitorio = e.resp.status in (429, 500, 502, 503, 504) or \
                "ateLimitExceeded" in str(e)
            if not transitorio or i == tentativas - 1:
                raise
            time.sleep(2 ** i)


def motivo_erro(e):
    if isinstance(e, HttpError):
        try:
            return json.loads(e.content)["error"]["message"]
        except Exception:
            return str(e)
    return str(e)


def nome_seguro(nome):
    n = SEGURO.sub("_", nome).strip().rstrip(".") or "sem_nome"
    if n.split(".")[0].upper() in RESERVADOS:
        n = "_" + n
    stem, ext = os.path.splitext(n)
    return stem[:max(1, 120 - len(ext))] + ext


def unico(usados, nome, fid):
    if nome.lower() in usados:
        stem, ext = os.path.splitext(nome)
        nome = f"{stem} ({fid[:6]}){ext}"
    usados.add(nome.lower())
    return nome


def prefixo_longo(p):
    # evita o limite de 260 caracteres do Windows
    s = str(p)
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        return Path("\\\\?\\" + s)
    return p


def url_do_google(url):
    """A exportação alternativa manda o token do usuário: só para domínios do Google."""
    m = re.match(r"^https://([a-z0-9.-]+)(?::443)?/", url or "")
    if not m:
        return False
    host = m.group(1)
    return host.endswith(".google.com") or host.endswith(".googleapis.com") \
        or host.endswith(".googleusercontent.com")


def md5_arquivo(caminho):
    h = hashlib.md5()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def formatar_tamanho(n):
    if n is None:
        return "—"
    n = float(n)
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1024


def ordenar(filhos):
    return sorted(filhos, key=lambda n: (not n.pasta, n.nome.casefold(), n.id))


def nomes_locais(no):
    """Nome de cada filho no disco. Estável entre execuções, mesmo com seleção diferente."""
    usados, saida = set(), {}
    for f in sorted(no.filhos, key=lambda n: (n.nome.casefold(), n.id)):
        local = nome_seguro(f.nome)
        if not f.pasta and f.mime in EXPORT:
            ext = EXPORT[f.mime][1]
            if not local.lower().endswith(ext):
                local += ext
        saida[f.id] = unico(usados, local, f.id)
    return saida


# ---------- acesso ao Drive ----------

class DriveAPI:
    """Camada fina sobre a API do Drive. Os testes trocam esta classe por uma falsa."""

    def __init__(self, svc):
        self.svc = svc

    def usuario(self):
        r = executar(self.svc.about().get(fields="user(emailAddress,displayName)"))
        u = r.get("user", {})
        return u.get("emailAddress") or u.get("displayName") or "conta conectada"

    def info(self, fid):
        return executar(self.svc.files().get(
            fileId=fid, fields="id,name,mimeType", supportsAllDrives=True))

    def listar(self, pasta_id):
        token = None
        while True:
            r = executar(self.svc.files().list(
                q=f"'{pasta_id}' in parents and trashed=false",
                fields="nextPageToken, files(id, name, mimeType, size, md5Checksum)",
                pageSize=1000, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True))
            yield from r.get("files", [])
            token = r.get("nextPageToken")
            if not token:
                break

    def baixar(self, no, alvo, ctx):
        parcial = alvo.with_name(alvo.name + ".part")
        try:
            if no.mime in EXPORT:
                try:
                    req = self.svc.files().export_media(
                        fileId=no.id, mimeType=EXPORT[no.mime][0])
                    self._transmitir(req, parcial, ctx)
                except HttpError as e:
                    if "exportSizeLimitExceeded" not in str(e) and \
                            "too large" not in str(e).lower():
                        raise
                    ctx.log("  arquivo grande: tentando exportação alternativa")
                    self._exportar_grande(no, parcial)
            else:
                if no.tamanho == 0:
                    parcial.write_bytes(b"")
                else:
                    req = self.svc.files().get_media(fileId=no.id, supportsAllDrives=True)
                    self._transmitir(req, parcial, ctx)
                if no.md5 and md5_arquivo(parcial) != no.md5:
                    raise ValueError("conferência (MD5) diferente do original")
            os.replace(parcial, alvo)
        except BaseException:
            try:
                if parcial.exists():
                    parcial.unlink()
            except OSError:
                pass
            raise

    def _transmitir(self, req, parcial, ctx):
        with open(parcial, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req, chunksize=CHUNK)
            pronto = False
            while not pronto:
                ctx.checar()
                _, pronto = dl.next_chunk(num_retries=5)

    def _exportar_grande(self, no, parcial):
        # a exportação normal tem limite de ~10 MB; o link de exportação costuma passar
        meta = executar(self.svc.files().get(
            fileId=no.id, fields="exportLinks", supportsAllDrives=True))
        url = (meta.get("exportLinks") or {}).get(EXPORT[no.mime][0])
        if not url:
            raise RuntimeError("arquivo grande demais para exportar; baixe pelo navegador")
        if not url_do_google(url):
            raise RuntimeError("link de exportação fora do Google; ignorado por segurança")
        resp, conteudo = self.svc._http.request(url)
        if resp.status != 200:
            raise RuntimeError(f"exportação alternativa falhou (HTTP {resp.status})")
        parcial.write_bytes(conteudo)

    def criar_pasta(self, nome, pai):
        r = executar(self.svc.files().create(
            body={"name": nome, "mimeType": FOLDER, "parents": [pai]},
            fields="id", supportsAllDrives=True))
        return r["id"]

    def enviar(self, caminho, pai, converter, ctx):
        p = Path(caminho)
        corpo = {"name": p.name, "parents": [pai]}
        if converter and p.suffix.lower() in CONVERTER:
            corpo["mimeType"] = CONVERTER[p.suffix.lower()]
            corpo["name"] = p.stem
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        vazio = p.stat().st_size == 0
        media = MediaFileUpload(str(p), mimetype=mime,
                                resumable=not vazio, chunksize=CHUNK)
        req = self.svc.files().create(body=corpo, media_body=media, fields="id",
                                      supportsAllDrives=True)
        if vazio:
            return req.execute()["id"]
        resp = None
        while resp is None:
            ctx.checar()
            _, resp = req.next_chunk(num_retries=5)
        return resp["id"]


def autenticar(token, escopo, credenciais, ctx=None, interativo=True):
    """Devolve um DriveAPI. Abre o navegador na primeira vez (ou quando o escopo muda)."""
    log = ctx.log if ctx else print
    token = Path(token)
    creds = None
    if token.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token))
        except Exception:
            creds = None
        escopos = (creds.scopes or []) if creds else []
        if creds and escopo not in escopos and SCOPE_TOTAL not in escopos:
            creds = None
    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                gravar_privado(token, creds.to_json())
            except RefreshError:
                creds = None
        else:
            creds = None
    if not creds:
        if not interativo:
            raise RuntimeError("login necessário")
        if not Path(credenciais).exists():
            raise FileNotFoundError(
                "Falta o credentials.json. Veja a aba Começar para criar e escolher o arquivo.")
        log("Abrindo o navegador para você autorizar o acesso...")
        fluxo = InstalledAppFlow.from_client_secrets_file(str(credenciais), [escopo])
        creds = fluxo.run_local_server(
            port=0, timeout_seconds=300, authorization_prompt_message="",
            success_message="Autorizado. Pode fechar esta aba e voltar ao Drive Clone.")
        gravar_privado(token, creds.to_json())
    return DriveAPI(build("drive", "v3", credentials=creds, cache_discovery=False))


# ---------- mapear ----------

def mapear(api, raiz_id, nome_raiz, ctx):
    raiz = No(raiz_id, nome_raiz, FOLDER)
    contagem = {"pastas": 0, "arquivos": 0}

    def rec(no):
        ctx.checar()
        for it in api.listar(no.id):
            tam = int(it["size"]) if it.get("size") else None
            filho = No(it["id"], it["name"], it["mimeType"], tam, it.get("md5Checksum"))
            no.filhos.append(filho)
            if filho.pasta:
                contagem["pastas"] += 1
                rec(filho)
            else:
                contagem["arquivos"] += 1
        if contagem["pastas"] % 20 == 0:
            ctx.log(f"Mapeando... {contagem['pastas']} pastas, {contagem['arquivos']} arquivos")

    rec(raiz)
    ctx.log(f"Mapeamento concluído: {contagem['pastas']} pastas, {contagem['arquivos']} arquivos.")
    return raiz


def mapear_local(pasta):
    pasta = Path(pasta)
    raiz = No(str(pasta), pasta.name or str(pasta), FOLDER)

    def rec(no, p):
        try:
            itens = sorted(p.iterdir(), key=lambda x: x.name.casefold())
        except OSError:
            return
        for item in itens:
            if item.name in IGNORAR or item.name.endswith((".part", ".tmp")):
                continue
            if item.is_symlink():
                continue
            if item.is_dir():
                filho = No(str(item), item.name, FOLDER)
                rec(filho, item)
            elif item.is_file():
                filho = No(str(item), item.name,
                           mimetypes.guess_type(item.name)[0] or "application/octet-stream",
                           item.stat().st_size)
            else:
                continue
            no.filhos.append(filho)

    rec(raiz, pasta)
    return raiz


def contar(raiz, selecionados=None):
    """(arquivos, bytes) marcados."""
    n = b = 0
    for x in raiz.percorrer():
        if x.pasta or (selecionados is not None and x.id not in selecionados):
            continue
        n += 1
        b += x.tamanho or 0
    return n, b


# ---------- baixar ----------

def _ja_existe(alvo, no):
    if not alvo.exists():
        return False
    if no.mime in EXPORT or no.tamanho is None:
        return True
    return alvo.stat().st_size == no.tamanho


def _celula_segura(valor):
    # nomes vêm de terceiros: "=..." viraria fórmula ao abrir o CSV no Excel
    texto = str(valor)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


def salvar_manifesto(linhas, caminho):
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["caminho", "tipo", "tamanho", "status",
                                          "detalhe", "id", "link"])
        w.writeheader()
        w.writerows({k: _celula_segura(v) for k, v in l.items()} for l in linhas)


def falhas(linhas):
    """Itens que o app não conseguiu baixar (erro ou tipo sem exportação)."""
    return [l for l in linhas if l["status"] in ("erro", "ignorado")]


def salvar_falhas(linhas, caminho):
    itens = falhas(linhas)
    caminho = Path(caminho)
    if not itens:
        if caminho.exists():
            caminho.unlink()
        return
    partes = [f"Arquivos para baixar manualmente ({len(itens)}). Abra o link no navegador.\n"]
    for l in itens:
        partes.append(f"[{l['status']}] {l['caminho']}\n    motivo: {l['detalhe'] or '-'}\n    link: {l['link']}\n")
    caminho.write_text("\n".join(partes), encoding="utf-8")


def baixar(api, raiz, destino, selecionados, ctx):
    """Baixa o que estiver em `selecionados` (None = tudo). Devolve (linhas, pasta_base)."""
    base = prefixo_longo(Path(destino).resolve() / nome_seguro(raiz.nome))
    marcado = lambda n: selecionados is None or n.id in selecionados
    total = sum(1 for n in raiz.percorrer() if not n.pasta and marcado(n))
    feito = [0]
    linhas = []

    def registrar(rel, no, status, detalhe=""):
        linhas.append({"caminho": rel, "tipo": no.mime.replace(GAPPS, ""),
                       "tamanho": "" if no.tamanho is None else no.tamanho,
                       "status": status, "detalhe": detalhe, "id": no.id,
                       "link": f"https://drive.google.com/open?id={no.id}"})
        ctx.log(f"[{status}] {rel}" + (f"  ({detalhe})" if detalhe else ""))

    def rec(no, local, rel):
        ctx.checar()
        local.mkdir(parents=True, exist_ok=True)
        nomes = nomes_locais(no)
        for f in ordenar(no.filhos):
            if not marcado(f):
                continue
            ctx.checar()
            nome_local = nomes[f.id]
            if f.pasta:
                rec(f, local / nome_local, f"{rel}{nome_local}/")
                continue
            caminho = rel + nome_local
            if f.mime == SHORTCUT:
                registrar(caminho, f, "ignorado", "atalho não seguido")
            elif f.mime.startswith(GAPPS) and f.mime not in EXPORT:
                registrar(caminho, f, "ignorado", "tipo do Google sem exportação")
            elif _ja_existe(local / nome_local, f):
                registrar(caminho, f, "já existia")
            else:
                try:
                    api.baixar(f, local / nome_local, ctx)
                    registrar(caminho, f, "baixado")
                except Cancelado:
                    raise
                except Exception as e:
                    registrar(caminho, f, "erro", motivo_erro(e))
            feito[0] += 1
            ctx.progresso(feito[0], total)

    try:
        rec(raiz, base, nome_seguro(raiz.nome) + "/")
    finally:
        if base.exists():
            salvar_manifesto(linhas, base / "_manifesto.csv")
            salvar_falhas(linhas, base / "_para_baixar_manualmente.txt")
    return linhas, base


def resumo(linhas):
    contagem = {}
    for l in linhas:
        contagem[l["status"]] = contagem.get(l["status"], 0) + 1
    return contagem


# ---------- enviar ----------

def _salvar_estado(arq, estado):
    tmp = arq.with_name(arq.name + ".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, arq)


def enviar(api, raiz, selecionados, pai, nome_raiz, converter, ctx, simular=False):
    """Envia a árvore local `raiz` para a pasta `pai` do Drive ("root" = Meu Drive)."""
    marcado = lambda n: selecionados is None or n.id in selecionados
    total = sum(1 for n in raiz.percorrer() if not n.pasta and marcado(n))
    arq_estado = Path(raiz.id) / ".estado_envio.json"
    estado = {}
    if not simular and arq_estado.exists():
        try:
            estado = json.loads(arq_estado.read_text(encoding="utf-8"))
        except Exception:
            ctx.log("Aviso: o registro de progresso anterior estava ilegível; recomeçando.")
    res = {"enviados": 0, "pulados": 0, "erros": 0}
    feito = [0]

    def pasta_destino(chave, nome, pai_id):
        if chave in estado:
            return estado[chave]
        fid = api.criar_pasta(nome, pai_id)
        estado[chave] = fid
        _salvar_estado(arq_estado, estado)
        return fid

    def rec(no, pai_id, rel):
        for f in ordenar(no.filhos):
            if not marcado(f):
                continue
            ctx.checar()
            caminho = rel + f.nome
            chave = f"{pai}|{caminho}"
            try:
                if f.pasta:
                    if simular:
                        ctx.log(f"[pasta] {caminho}")
                        rec(f, None, caminho + "/")
                    else:
                        rec(f, pasta_destino(chave, f.nome, pai_id), caminho + "/")
                    continue
                if simular:
                    ctx.log(f"[arquivo] {caminho}  ({formatar_tamanho(f.tamanho)})")
                    res["enviados"] += 1
                elif chave in estado:
                    res["pulados"] += 1
                else:
                    estado[chave] = api.enviar(f.id, pai_id, converter, ctx)
                    _salvar_estado(arq_estado, estado)
                    res["enviados"] += 1
                    ctx.log(f"[enviado] {caminho}")
            except Cancelado:
                raise
            except Exception as e:
                res["erros"] += 1
                ctx.log(f"[erro] {caminho}  ({motivo_erro(e)})")
                continue
            feito[0] += 1
            ctx.progresso(feito[0], total)

    if simular:
        ctx.log(f"Simulação: nada será enviado. Pasta criada no destino: {nome_raiz}/")
        rec(raiz, None, nome_raiz + "/")
        return res

    if pai != "root":
        meta = api.info(pai)
        if meta.get("mimeType") != FOLDER:
            raise ValueError("O link de destino não é de uma pasta.")
    raiz_id = pasta_destino(f"{pai}|{nome_raiz}", nome_raiz, pai)
    rec(raiz, raiz_id, nome_raiz + "/")
    return res
