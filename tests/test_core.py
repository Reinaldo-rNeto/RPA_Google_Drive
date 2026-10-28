# -*- coding: utf-8 -*-
"""Testes do motor com um Drive falso (sem rede). Rodar: python -m unittest discover tests"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import core  # noqa: E402

DOC = "application/vnd.google-apps.document"
PLAN = "application/vnd.google-apps.spreadsheet"
FORM = "application/vnd.google-apps.form"


class DriveFalso:
    """Mesma interface do core.DriveAPI, tudo em memória."""

    def __init__(self, itens, conteudos):
        self.itens = itens          # pasta_id -> lista de dicts da API
        self.conteudos = conteudos  # id -> bytes
        self.baixados = []
        self.criadas = []           # (nome, pai, id)
        self.enviados = []          # (caminho, pai)
        self.falhar = set()
        self.seq = 0

    def listar(self, pasta_id):
        return list(self.itens.get(pasta_id, []))

    def info(self, fid):
        return {"id": fid, "name": fid, "mimeType": core.FOLDER}

    def baixar(self, no, alvo, ctx):
        if no.id in self.falhar:
            raise RuntimeError("download bloqueado pelo dono")
        self.baixados.append(no.id)
        alvo.write_bytes(self.conteudos[no.id])

    def criar_pasta(self, nome, pai):
        self.seq += 1
        fid = f"nova{self.seq}"
        self.criadas.append((nome, pai, fid))
        return fid

    def enviar(self, caminho, pai, converter, ctx):
        self.seq += 1
        self.enviados.append((Path(caminho).name, pai))
        return f"arq{self.seq}"


def item(i, nome, mime="text/plain", tam=None, md5=None):
    d = {"id": i, "name": nome, "mimeType": mime}
    if tam is not None:
        d["size"] = str(tam)
    if md5:
        d["md5Checksum"] = md5
    return d


def montar():
    itens = {
        "raiz": [item("p1", "Redmine", core.FOLDER), item("p2", "Power_BI", core.FOLDER),
                 item("f0", "leia-me.txt", tam=2), item("g1", "Plano", PLAN)],
        "p1": [item("p3", "TR", core.FOLDER), item("f1", "a.txt", tam=1),
               item("f2", "A.txt", tam=1)],           # colisão sem diferenciar maiúsculas
        "p3": [item("f3", "b?.txt", tam=3), item("g2", "Ata", DOC), item("x1", "Form", FORM)],
        "p2": [],
    }
    conteudos = {"f0": b"oi", "f1": b"1", "f2": b"2", "f3": b"333", "g1": b"xlsx", "g2": b"docx"}
    return DriveFalso(itens, conteudos)


class TestMotor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.api = montar()
        self.logs = []
        self.ctx = core.Ctx(log=self.logs.append)
        self.raiz = core.mapear(self.api, "raiz", "GDA", self.ctx)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mapear_monta_a_arvore(self):
        nomes = [n.nome for n in self.raiz.percorrer()]
        self.assertEqual(nomes.count("TR"), 1)
        self.assertEqual(len(nomes), 11)
        self.assertEqual(core.contar(self.raiz)[0], 7)

    def test_baixar_tudo_mantem_hierarquia(self):
        linhas, base = core.baixar(self.api, self.raiz, self.dir, None, self.ctx)
        self.assertTrue((base / "leia-me.txt").read_bytes() == b"oi")
        self.assertTrue((base / "Plano.xlsx").exists())
        self.assertTrue((base / "Redmine" / "TR" / "Ata.docx").exists())
        self.assertTrue((base / "Redmine" / "TR" / "b_.txt").exists())
        self.assertTrue((base / "Power_BI").is_dir())
        self.assertTrue((base / "_manifesto.csv").exists())
        r = core.resumo(linhas)
        self.assertEqual(r["baixado"], 6)
        self.assertEqual(r["ignorado"], 1)  # formulário do Google

    def test_nomes_que_colidem_nao_se_sobrescrevem(self):
        _, base = core.baixar(self.api, self.raiz, self.dir, None, self.ctx)
        arquivos = sorted(p.name.lower() for p in (base / "Redmine").glob("*.txt"))
        self.assertEqual(len(arquivos), 2)
        self.assertEqual(len(set(arquivos)), 2)

    def test_baixar_so_o_selecionado(self):
        sel = {"raiz", "p1", "p3", "f3"}
        _, base = core.baixar(self.api, self.raiz, self.dir, sel, self.ctx)
        self.assertTrue((base / "Redmine" / "TR" / "b_.txt").exists())
        self.assertFalse((base / "leia-me.txt").exists())
        self.assertFalse((base / "Power_BI").exists())
        self.assertEqual(self.api.baixados, ["f3"])

    def test_retomar_nao_baixa_de_novo(self):
        core.baixar(self.api, self.raiz, self.dir, None, self.ctx)
        n = len(self.api.baixados)
        linhas, _ = core.baixar(self.api, self.raiz, self.dir, None, self.ctx)
        self.assertEqual(len(self.api.baixados), n)
        self.assertEqual(core.resumo(linhas).get("já existia"), 6)

    def test_erro_em_um_arquivo_nao_para_os_outros(self):
        self.api.falhar = {"f3"}
        linhas, base = core.baixar(self.api, self.raiz, self.dir, None, self.ctx)
        r = core.resumo(linhas)
        self.assertEqual(r["erro"], 1)
        self.assertTrue((base / "leia-me.txt").exists())
        self.assertFalse((base / "Redmine" / "TR" / "b_.txt").exists())

    def test_lista_para_baixar_manualmente(self):
        self.api.falhar = {"f3"}
        linhas, base = core.baixar(self.api, self.raiz, self.dir, None, self.ctx)
        texto = (base / "_para_baixar_manualmente.txt").read_text(encoding="utf-8")
        self.assertIn("[erro] GDA/Redmine/TR/b_.txt", texto)
        self.assertIn("download bloqueado pelo dono", texto)
        self.assertIn("id=f3", texto)
        self.assertIn("[ignorado] GDA/Redmine/TR/Form", texto)   # formulário do Google
        self.assertEqual(len(core.falhas(linhas)), 2)
        # sem falhas, o arquivo não fica sobrando
        self.api.falhar = set()
        core.baixar(self.api, self.raiz, self.dir, {"raiz", "f0"}, self.ctx)
        self.assertFalse((base / "_para_baixar_manualmente.txt").exists())

    def test_cancelar_guarda_o_manifesto(self):
        ev = threading.Event()
        ctx = core.Ctx(log=lambda m: ev.set() if m.startswith("[baixado]") else None,
                       cancelar=ev)
        with self.assertRaises(core.Cancelado):
            core.baixar(self.api, self.raiz, self.dir, None, ctx)
        self.assertTrue((self.dir / "GDA" / "_manifesto.csv").exists())
        self.assertLess(len(self.api.baixados), 6)

    def test_enviar_simulacao_nao_chama_o_drive(self):
        core.baixar(self.api, self.raiz, self.dir, None, self.ctx)
        local = core.mapear_local(self.dir / "GDA")
        destino = montar()
        res = core.enviar(destino, local, None, "root", "GDA", False, self.ctx, simular=True)
        self.assertEqual(res["enviados"], 6)
        self.assertEqual(destino.criadas, [])
        self.assertEqual(destino.enviados, [])

    def test_enviar_recria_estrutura_e_retoma(self):
        core.baixar(self.api, self.raiz, self.dir, None, self.ctx)
        local = core.mapear_local(self.dir / "GDA")
        nomes_locais = {n.nome for n in local.percorrer()}
        self.assertNotIn("_manifesto.csv", nomes_locais)
        destino = montar()
        res = core.enviar(destino, local, None, "root", "Copia", False, self.ctx)
        self.assertEqual(res["enviados"], 6)
        self.assertEqual(res["erros"], 0)
        self.assertEqual({c[0] for c in destino.criadas},
                         {"Copia", "Redmine", "TR", "Power_BI"})
        # o arquivo da pasta TR foi para a pasta TR criada no destino
        id_tr = [c[2] for c in destino.criadas if c[0] == "TR"][0]
        self.assertIn(("Ata.docx", id_tr), destino.enviados)
        # segunda execução: nada novo
        destino2 = montar()
        res2 = core.enviar(destino2, core.mapear_local(self.dir / "GDA"),
                           None, "root", "Copia", False, self.ctx)
        self.assertEqual(res2["enviados"], 0)
        self.assertEqual(res2["pulados"], 6)
        self.assertEqual(destino2.enviados, [])

    def test_enviar_so_o_selecionado(self):
        core.baixar(self.api, self.raiz, self.dir, None, self.ctx)
        local = core.mapear_local(self.dir / "GDA")
        leia = [n for n in local.percorrer() if n.nome == "leia-me.txt"][0]
        destino = montar()
        res = core.enviar(destino, local, {local.id, leia.id}, "root", "Parcial",
                          False, self.ctx)
        self.assertEqual(res["enviados"], 1)
        self.assertEqual([c[0] for c in destino.criadas], ["Parcial"])


class TestSeguranca(unittest.TestCase):
    def test_manifesto_nao_vira_formula(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.csv"
            core.salvar_manifesto([{"caminho": "=HYPERLINK(\"http://x\")", "tipo": "t", "tamanho": 1,
                                    "status": "baixado", "detalhe": "@cmd", "id": "i", "link": "l"}], p)
            texto = p.read_text(encoding="utf-8-sig")
            self.assertIn("'=HYPERLINK", texto)
            self.assertIn("'@cmd", texto)

    @unittest.skipIf(os.name == "nt", "permissões POSIX")
    def test_segredos_so_o_dono_le(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "token.json"
            core.gravar_privado(p, "{}")
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)


class TestUtilidades(unittest.TestCase):
    def test_extrair_id(self):
        self.assertEqual(core.extrair_id(
            "https://drive.google.com/drive/folders/12q4TS37Hfo53?usp=sharing"), "12q4TS37Hfo53")
        self.assertEqual(core.extrair_id("https://drive.google.com/open?id=abc_123"), "abc_123")
        self.assertEqual(core.extrair_id("  abc123  "), "abc123")

    def test_nome_seguro(self):
        self.assertEqual(core.nome_seguro('a/b:c?.docx'), "a_b_c_.docx")
        self.assertEqual(core.nome_seguro("CON"), "_CON")
        longo = core.nome_seguro("x" * 300 + ".pptx")
        self.assertEqual(len(longo), 120)
        self.assertTrue(longo.endswith(".pptx"))
        self.assertEqual(core.nome_seguro("fim. "), "fim")

    def test_nomes_perigosos_nao_saem_da_pasta(self):
        for nome in ("..", "../x", "..\\..\\x", "C:\\Windows\\x", "/etc/passwd", "a:b.txt", "...", " . "):
            n = core.nome_seguro(nome)
            self.assertNotIn("/", n)
            self.assertNotIn("\\", n)
            self.assertNotIn(":", n)
            self.assertNotIn(n, ("", ".", ".."))

    def test_id_invalido_e_recusado(self):
        for ruim in ("", "   ", "x' or name contains 'a", "a b", "a/b"):
            with self.assertRaises(ValueError):
                core.extrair_id(ruim)
        self.assertEqual(core.extrair_id("root"), "root")

    def test_url_de_exportacao(self):
        self.assertTrue(core.url_do_google("https://docs.google.com/document/d/x/export?format=docx"))
        self.assertTrue(core.url_do_google("https://www.googleapis.com/drive/v3/files/x/export"))
        for ruim in ("http://docs.google.com/x", "https://evil.com/x", "https://docs.google.com.evil.com/x",
                     "https://evil.com/docs.google.com/", "", None):
            self.assertFalse(core.url_do_google(ruim))

    def test_md5(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.bin"
            p.write_bytes(b"abc")
            self.assertEqual(core.md5_arquivo(p), "900150983cd24fb0d6963f7d28e17f72")

    def test_validar_credenciais(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text('{"installed": {"client_id": "x"}}', encoding="utf-8")
            self.assertTrue(core.validar_credenciais(p)[0])
            p.write_text('{"web": {"client_id": "x"}}', encoding="utf-8")
            self.assertFalse(core.validar_credenciais(p)[0])
            p.write_text("nao e json", encoding="utf-8")
            self.assertFalse(core.validar_credenciais(p)[0])

    def test_procurar_credenciais_ao_lado(self):
        with tempfile.TemporaryDirectory() as d:
            pasta = Path(d)
            self.assertIsNone(core.procurar_credenciais([pasta]))
            (pasta / "client_secret_123.apps.googleusercontent.com.json").write_text(
                '{"installed": {"client_id": "x"}}', encoding="utf-8")
            achado = core.procurar_credenciais([pasta / "nao_existe", pasta])
            self.assertEqual(achado.name, "client_secret_123.apps.googleusercontent.com.json")
            (pasta / "client_secret_123.apps.googleusercontent.com.json").write_text(
                '{"web": {"client_id": "x"}}', encoding="utf-8")
            self.assertIsNone(core.procurar_credenciais([pasta]))

    def test_formatar_tamanho(self):
        self.assertEqual(core.formatar_tamanho(None), "—")
        self.assertEqual(core.formatar_tamanho(512), "512 B")
        self.assertEqual(core.formatar_tamanho(1536), "1.5 KB")


if __name__ == "__main__":
    unittest.main()
