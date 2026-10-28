# -*- coding: utf-8 -*-
"""Janela do Drive Clone (customtkinter)."""

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from app import core

LINKS = {
    "projeto": "https://console.cloud.google.com/projectcreate",
    "api": "https://console.cloud.google.com/apis/library/drive.googleapis.com",
    "consentimento": "https://console.cloud.google.com/apis/credentials/consent",
    "credenciais": "https://console.cloud.google.com/apis/credentials",
}

PASSOS = (
    "1. Crie um projeto no Google Cloud (qualquer nome, por exemplo DriveClone).\n"
    "2. Ative a \"Google Drive API\" nesse projeto.\n"
    "3. Configure a tela de consentimento: tipo Externo, nome do app \"Drive Clone\" e seu "
    "e-mail como contato. Em Público-alvo (ou Usuários de teste), adicione o e-mail da conta "
    "que vai usar para baixar e, se for enviar, o da conta de destino.\n"
    "4. Em Credenciais, crie um ID do cliente OAuth do tipo \"App para computador\" e baixe o "
    "arquivo JSON.\n"
    "5. Volte aqui, clique em \"Escolher arquivo...\" e selecione o JSON baixado.\n"
    "6. No primeiro login o Google avisa \"app não verificado\": clique em Avançado e depois em "
    "\"Acessar Drive Clone\". O app é seu, o aviso é normal.\n\n"
    "Enquanto o app está em modo teste, o login vale por 7 dias. Depois disso é só entrar de novo."
)


def abrir_no_sistema(caminho):
    try:
        if os.name == "nt":
            os.startfile(str(caminho))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(caminho)])
        else:
            subprocess.Popen(["xdg-open", str(caminho)])
    except Exception:
        pass


class ArvoreMarcavel(ttk.Frame):
    """Árvore de pastas e arquivos com caixas de seleção ([x] marcado, [-] parcial)."""

    PREFIXO = {0: "[  ] ", 1: "[x] ", 2: "[-] "}

    def __init__(self, master, ao_mudar=None):
        super().__init__(master)
        self.ao_mudar = ao_mudar
        self.tree = ttk.Treeview(self, columns=("tam",), selectmode="browse")
        self.tree.heading("#0", text="Nome", anchor="w")
        self.tree.heading("tam", text="Tamanho", anchor="e")
        self.tree.column("#0", stretch=True, minwidth=240)
        self.tree.column("tam", width=110, stretch=False, anchor="e")
        barra = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=barra.set)
        self.tree.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._clique)
        self.tree.bind("<space>", self._espaco)
        self.nos = {}
        self.estado = {}
        self.raiz = None

    def limpar(self):
        self.tree.delete(*self.tree.get_children())
        self.nos.clear()
        self.estado.clear()
        self.raiz = None

    def carregar(self, raiz):
        self.limpar()
        self.raiz = raiz

        def add(no, pai_iid):
            iid = self.tree.insert(pai_iid, "end", text=self.PREFIXO[1] + no.nome,
                                   values=("" if no.pasta else core.formatar_tamanho(no.tamanho),),
                                   open=pai_iid == "")
            self.nos[iid] = no
            self.estado[iid] = 1
            for f in core.ordenar(no.filhos):
                add(f, iid)

        add(raiz, "")
        self._notificar()

    def _texto(self, iid):
        self.tree.item(iid, text=self.PREFIXO[self.estado[iid]] + self.nos[iid].nome)

    def _definir_subarvore(self, iid, valor):
        pilha = [iid]
        while pilha:
            atual = pilha.pop()
            self.estado[atual] = valor
            self._texto(atual)
            pilha.extend(self.tree.get_children(atual))

    def _recalcular_acima(self, iid):
        while iid:
            filhos = self.tree.get_children(iid)
            if filhos:
                estados = {self.estado[c] for c in filhos}
                self.estado[iid] = 1 if estados == {1} else 0 if estados == {0} else 2
                self._texto(iid)
            iid = self.tree.parent(iid)

    def alternar(self, iid):
        novo = 0 if self.estado[iid] == 1 else 1
        self._definir_subarvore(iid, novo)
        self._recalcular_acima(self.tree.parent(iid))
        self._notificar()

    def marcar_tudo(self, valor):
        for iid in self.tree.get_children(""):
            self._definir_subarvore(iid, 1 if valor else 0)
        self._notificar()

    def _clique(self, ev):
        if self.tree.identify_region(ev.x, ev.y) != "tree":
            return
        if "indicator" in self.tree.identify_element(ev.x, ev.y):
            return
        iid = self.tree.identify_row(ev.y)
        if iid:
            self.alternar(iid)

    def _espaco(self, _ev):
        sel = self.tree.selection()
        if sel:
            self.alternar(sel[0])
        return "break"

    def _notificar(self):
        if self.ao_mudar:
            self.ao_mudar()

    def marcados(self):
        """Ids dos nós marcados ou parcialmente marcados (as pastas no caminho entram)."""
        return {self.nos[i].id for i, e in self.estado.items() if e in (1, 2)}

    def contagem(self):
        n = b = 0
        for iid, e in self.estado.items():
            no = self.nos[iid]
            if e == 1 and not no.pasta:
                n += 1
                b += no.tamanho or 0
        return n, b

    def estilizar(self, escuro):
        estilo = ttk.Style(self)
        try:
            estilo.theme_use("clam")
        except Exception:
            pass
        fundo, texto, cab = ("#2b2b2b", "#e6e6e6", "#3a3a3a") if escuro else \
            ("#ffffff", "#1a1a1a", "#e6e6e6")
        estilo.configure("Treeview", background=fundo, fieldbackground=fundo,
                         foreground=texto, rowheight=24, borderwidth=0)
        estilo.configure("Treeview.Heading", background=cab, foreground=texto,
                         relief="flat")
        estilo.map("Treeview", background=[("selected", "#1f6aa5")],
                   foreground=[("selected", "#ffffff")])


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        self.title("Drive Clone")
        self.geometry("1020x760")
        self.minsize(900, 680)

        self.cfg = core.carregar_config()
        self.fila = queue.Queue()
        self.cancelar = threading.Event()
        self.ocupado = False
        self.api_origem = None
        self.api_destino = None
        self.raiz_baixar = None
        self.raiz_enviar = None
        self.ultima_saida = None
        self.ui = {}
        self.botoes_acao = []

        self.abas = ctk.CTkTabview(self)
        self.abas.pack(fill="both", expand=True, padx=12, pady=12)
        for nome in ("Começar", "Baixar", "Enviar"):
            self.abas.add(nome)
        self._montar_comecar(self.abas.tab("Começar"))
        self._montar_baixar(self.abas.tab("Baixar"))
        self._montar_enviar(self.abas.tab("Enviar"))

        escuro = ctk.get_appearance_mode() == "Dark"
        self.arv_baixar.estilizar(escuro)

        self._atualizar_credenciais()
        self._login_silencioso()
        self.after(80, self._processar_fila)
        self.protocol("WM_DELETE_WINDOW", self._fechar)

    # ---------- montagem ----------

    def _montar_comecar(self, aba):
        rolagem = ctk.CTkScrollableFrame(aba, fg_color="transparent")
        rolagem.pack(fill="both", expand=True)
        rolagem.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(rolagem, text="Drive Clone", font=ctk.CTkFont(size=24, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=6, pady=(4, 0))
        ctk.CTkLabel(rolagem, text="Baixa uma pasta do Google Drive com todas as subpastas "
                     "e, se quiser, envia para outro Drive.", justify="left"
                     ).grid(row=1, column=0, sticky="w", padx=6, pady=(0, 10))

        card = ctk.CTkFrame(rolagem)
        card.grid(row=2, column=0, sticky="ew", padx=6, pady=4)
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(card, text="O que você precisa", font=ctk.CTkFont(size=15, weight="bold")
                     ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 4))

        self.st_cred = ctk.CTkLabel(card, text="", width=150, anchor="w")
        self.st_origem = ctk.CTkLabel(card, text="", width=150, anchor="w")
        self.st_destino = ctk.CTkLabel(card, text="", width=150, anchor="w")
        linhas = [
            (self.st_cred, "Arquivo credentials.json",
             "A chave do app para falar com o Drive. Criada uma vez, de graça, no Google "
             "Cloud (passo a passo abaixo).", "Escolher arquivo...", self._escolher_credenciais),
            (self.st_origem, "Conta de origem",
             "A conta do Google que abre a pasta compartilhada no navegador.",
             "Entrar / trocar", self._entrar_origem),
            (None, "Link da pasta",
             "No Drive: botão direito na pasta, Compartilhar, Copiar link. Cole na aba Baixar.",
             None, None),
            (None, "Permissão para baixar",
             "O dono não pode ter bloqueado o download. Arquivo bloqueado aparece como erro "
             "no log e os demais continuam.", None, None),
            (self.st_destino, "Conta de destino",
             "Só para enviar. Pode ser a mesma ou outra conta. Sem link de pasta de destino, "
             "a cópia vai para a raiz do Meu Drive e o app pede só a permissão mínima. Com link, "
             "pede acesso ao Drive dessa conta.", "Entrar / trocar", self._entrar_destino),
        ]
        for i, (status, titulo, desc, rotulo, cmd) in enumerate(linhas, start=1):
            if status is not None:
                status.grid(in_=card, row=i, column=0, sticky="nw", padx=(12, 6), pady=6)
            else:
                ctk.CTkLabel(card, text="Você informa" if titulo == "Link da pasta"
                             else "Do dono do arquivo", width=150, anchor="w",
                             text_color=("gray40", "gray65")).grid(
                    row=i, column=0, sticky="nw", padx=(12, 6), pady=6)
            bloco = ctk.CTkFrame(card, fg_color="transparent")
            bloco.grid(row=i, column=1, sticky="ew", pady=4)
            ctk.CTkLabel(bloco, text=titulo, font=ctk.CTkFont(weight="bold"), anchor="w"
                         ).pack(anchor="w")
            ctk.CTkLabel(bloco, text=desc, wraplength=520, justify="left", anchor="w",
                         text_color=("gray30", "gray70")).pack(anchor="w")
            if rotulo:
                ctk.CTkButton(card, text=rotulo, width=140, command=cmd).grid(
                    row=i, column=2, padx=12, pady=6, sticky="ne")

        passos = ctk.CTkFrame(rolagem)
        passos.grid(row=3, column=0, sticky="ew", padx=6, pady=(10, 4))
        passos.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(passos, text="Como criar o credentials.json (uma vez só)",
                     font=ctk.CTkFont(size=15, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        caixa = ctk.CTkTextbox(passos, height=250, wrap="word")
        caixa.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        caixa.insert("1.0", PASSOS)
        caixa.configure(state="disabled")
        botoes = ctk.CTkFrame(passos, fg_color="transparent")
        botoes.grid(row=2, column=0, sticky="w", padx=12, pady=(4, 10))
        for rotulo, chave in (("1. Criar projeto", "projeto"), ("2. Ativar Drive API", "api"),
                              ("3. Tela de consentimento", "consentimento"),
                              ("4. Criar credencial", "credenciais")):
            ctk.CTkButton(botoes, text=rotulo, width=170,
                          command=lambda c=chave: webbrowser.open(LINKS[c])
                          ).pack(side="left", padx=(0, 8))

        rodape = ctk.CTkFrame(rolagem, fg_color="transparent")
        rodape.grid(row=4, column=0, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(rodape, text=f"Seus dados (chave e logins) ficam em: {core.pasta_dados()}",
                     text_color=("gray30", "gray70"), anchor="w", justify="left",
                     wraplength=700).pack(side="left")
        ctk.CTkButton(rodape, text="Abrir pasta", width=100,
                      command=lambda: abrir_no_sistema(core.pasta_dados())
                      ).pack(side="left", padx=10)

    def _montar_log(self, aba, nome, linha):
        barra = ctk.CTkProgressBar(aba)
        barra.set(0)
        barra.grid(row=linha, column=0, columnspan=4, sticky="ew", padx=6, pady=(6, 0))
        rotulo = ctk.CTkLabel(aba, text="", anchor="w")
        rotulo.grid(row=linha + 1, column=0, columnspan=4, sticky="ew", padx=6)
        log = ctk.CTkTextbox(aba, height=150, wrap="word")
        log.grid(row=linha + 2, column=0, columnspan=4, sticky="ew", padx=6, pady=(0, 4))
        log.configure(state="disabled")
        self.ui[nome] = {"barra": barra, "rotulo": rotulo, "log": log}

    def _montar_baixar(self, aba):
        aba.grid_columnconfigure(1, weight=1)
        aba.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(aba, text="Link ou ID da pasta").grid(row=0, column=0, sticky="w", padx=6)
        self.e_link = ctk.CTkEntry(aba, placeholder_text="https://drive.google.com/drive/folders/...")
        self.e_link.grid(row=0, column=1, columnspan=3, sticky="ew", padx=6, pady=3)
        self._preencher(self.e_link, self.cfg.get("link_origem"))
        ctk.CTkLabel(aba, text="Salvar em").grid(row=1, column=0, sticky="w", padx=6)
        self.e_saida = ctk.CTkEntry(aba)
        self.e_saida.grid(row=1, column=1, columnspan=2, sticky="ew", padx=6, pady=3)
        self.e_saida.insert(0, self.cfg.get("pasta_saida", str(Path.home() / "Downloads")))
        ctk.CTkButton(aba, text="Escolher...", width=100, command=self._escolher_saida
                      ).grid(row=1, column=3, padx=6)

        acoes = ctk.CTkFrame(aba, fg_color="transparent")
        acoes.grid(row=2, column=0, columnspan=4, sticky="ew", padx=6, pady=4)
        b_map = ctk.CTkButton(acoes, text="Mapear pasta", width=130, command=self._mapear_origem)
        b_map.pack(side="left")
        self.b_baixar = ctk.CTkButton(acoes, text="Baixar selecionados", width=160,
                                      command=self._baixar, state="disabled")
        self.b_baixar.pack(side="left", padx=8)
        self.b_parar_b = ctk.CTkButton(acoes, text="Parar", width=80, fg_color="#a33",
                                       hover_color="#822", command=self.cancelar.set,
                                       state="disabled")
        self.b_parar_b.pack(side="left")
        self.b_abrir = ctk.CTkButton(acoes, text="Abrir pasta baixada", width=150,
                                     command=lambda: abrir_no_sistema(self.ultima_saida),
                                     state="disabled")
        self.b_abrir.pack(side="right")
        self.botoes_acao += [b_map]

        self.arv_baixar = ArvoreMarcavel(aba, ao_mudar=self._atualizar_contagem_baixar)
        self.arv_baixar.grid(row=3, column=0, columnspan=4, sticky="nsew", padx=6, pady=4)
        marc = ctk.CTkFrame(aba, fg_color="transparent")
        marc.grid(row=4, column=0, columnspan=4, sticky="ew", padx=6)
        ctk.CTkButton(marc, text="Marcar tudo", width=110,
                      command=lambda: self.arv_baixar.marcar_tudo(True)).pack(side="left")
        ctk.CTkButton(marc, text="Desmarcar tudo", width=120,
                      command=lambda: self.arv_baixar.marcar_tudo(False)).pack(side="left", padx=8)
        self.l_cont_baixar = ctk.CTkLabel(marc, text="Mapeie a pasta para escolher o que baixar.")
        self.l_cont_baixar.pack(side="left", padx=10)
        self._montar_log(aba, "Baixar", 5)

    def _montar_enviar(self, aba):
        aba.grid_columnconfigure(1, weight=1)
        aba.grid_rowconfigure(5, weight=1)
        ctk.CTkLabel(aba, text="Pasta no computador").grid(row=0, column=0, sticky="w", padx=6)
        self.e_local = ctk.CTkEntry(aba, placeholder_text="a pasta que o Baixar criou")
        self.e_local.grid(row=0, column=1, columnspan=2, sticky="ew", padx=6, pady=3)
        self._preencher(self.e_local, self.cfg.get("pasta_enviar"))
        ctk.CTkButton(aba, text="Escolher...", width=100, command=self._escolher_local
                      ).grid(row=0, column=3, padx=6)
        ctk.CTkLabel(aba, text="Pasta de destino no Drive").grid(row=1, column=0, sticky="w", padx=6)
        self.e_destino = ctk.CTkEntry(
            aba, placeholder_text="link da pasta (vazio = raiz do Meu Drive)")
        self.e_destino.grid(row=1, column=1, columnspan=3, sticky="ew", padx=6, pady=3)
        self._preencher(self.e_destino, self.cfg.get("link_destino"))
        ctk.CTkLabel(aba, text="Nome da pasta criada").grid(row=2, column=0, sticky="w", padx=6)
        self.e_nome = ctk.CTkEntry(aba, placeholder_text="vazio = nome da pasta local")
        self.e_nome.grid(row=2, column=1, sticky="ew", padx=6, pady=3)
        self.v_conv = ctk.BooleanVar(value=bool(self.cfg.get("converter", False)))
        ctk.CTkCheckBox(aba, text="Converter .docx/.xlsx/.pptx em Docs, Planilhas e Apresentações",
                        variable=self.v_conv).grid(row=2, column=2, columnspan=2, sticky="w", padx=6)

        acoes = ctk.CTkFrame(aba, fg_color="transparent")
        acoes.grid(row=3, column=0, columnspan=4, sticky="ew", padx=6, pady=4)
        b_car = ctk.CTkButton(acoes, text="Carregar pasta", width=130, command=self._carregar_local)
        b_car.pack(side="left")
        self.b_enviar = ctk.CTkButton(acoes, text="Enviar selecionados", width=160,
                                      command=lambda: self._enviar(False), state="disabled")
        self.b_enviar.pack(side="left", padx=8)
        self.b_simular = ctk.CTkButton(acoes, text="Simular", width=90,
                                       command=lambda: self._enviar(True), state="disabled")
        self.b_simular.pack(side="left")
        self.b_parar_e = ctk.CTkButton(acoes, text="Parar", width=80, fg_color="#a33",
                                       hover_color="#822", command=self.cancelar.set,
                                       state="disabled")
        self.b_parar_e.pack(side="left", padx=8)
        b_ent = ctk.CTkButton(acoes, text="Entrar na conta de destino", width=190,
                              command=self._entrar_destino)
        b_ent.pack(side="right")
        self.botoes_acao += [b_car, b_ent]
        self.l_conta_dest = ctk.CTkLabel(aba, text="", anchor="w")
        self.l_conta_dest.grid(row=4, column=0, columnspan=4, sticky="ew", padx=6)

        self.arv_enviar = ArvoreMarcavel(aba, ao_mudar=self._atualizar_contagem_enviar)
        self.arv_enviar.grid(row=5, column=0, columnspan=4, sticky="nsew", padx=6, pady=4)
        marc = ctk.CTkFrame(aba, fg_color="transparent")
        marc.grid(row=6, column=0, columnspan=4, sticky="ew", padx=6)
        ctk.CTkButton(marc, text="Marcar tudo", width=110,
                      command=lambda: self.arv_enviar.marcar_tudo(True)).pack(side="left")
        ctk.CTkButton(marc, text="Desmarcar tudo", width=120,
                      command=lambda: self.arv_enviar.marcar_tudo(False)).pack(side="left", padx=8)
        self.l_cont_enviar = ctk.CTkLabel(marc, text="Carregue a pasta para escolher o que enviar.")
        self.l_cont_enviar.pack(side="left", padx=10)
        self._montar_log(aba, "Enviar", 7)

    # ---------- estado visual ----------

    @staticmethod
    def _preencher(campo, valor):
        # inserir texto vazio apaga o texto de exemplo do campo
        if valor:
            campo.insert(0, valor)

    @staticmethod
    def _pastas_do_app():
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parent.parent
        return [base, Path.cwd()]

    def _atualizar_credenciais(self):
        if not core.caminho_credenciais().exists():
            achado = core.procurar_credenciais(self._pastas_do_app())
            if achado:
                try:
                    core.importar_credenciais(achado)
                    self._escrever_log("Baixar", f"Credenciais encontradas ao lado do programa "
                                                 f"({achado.name}) e importadas.")
                except (ValueError, OSError):
                    pass
        ok = core.caminho_credenciais().exists()
        self.st_cred.configure(text="OK, encontrado" if ok else "Falta",
                               text_color=("#1a7f37", "#4cc26b") if ok else ("#b42318", "#ff7b72"))
        if not ok:
            self._status_conta(self.st_origem, None)
            self._status_conta(self.st_destino, None)

    def _status_conta(self, rotulo, email):
        if email:
            rotulo.configure(text="Conectada: " + email, text_color=("#1a7f37", "#4cc26b"))
        else:
            rotulo.configure(text="Não conectada", text_color=("#b42318", "#ff7b72"))

    def _atualizar_contagem_baixar(self):
        n, b = self.arv_baixar.contagem()
        self.l_cont_baixar.configure(text=f"{n} arquivos marcados ({core.formatar_tamanho(b)})")
        self.b_baixar.configure(state="normal" if n and not self.ocupado else "disabled")

    def _atualizar_contagem_enviar(self):
        n, b = self.arv_enviar.contagem()
        self.l_cont_enviar.configure(text=f"{n} arquivos marcados ({core.formatar_tamanho(b)})")
        estado = "normal" if n and not self.ocupado else "disabled"
        self.b_enviar.configure(state=estado)
        self.b_simular.configure(state=estado)

    def _set_ocupado(self, valor):
        self.ocupado = valor
        for b in self.botoes_acao:
            b.configure(state="disabled" if valor else "normal")
        for b in (self.b_parar_b, self.b_parar_e):
            b.configure(state="normal" if valor else "disabled")
        if valor:
            self.b_baixar.configure(state="disabled")
            self.b_enviar.configure(state="disabled")
            self.b_simular.configure(state="disabled")
        else:
            if self.arv_baixar.raiz:
                self._atualizar_contagem_baixar()
            if self.arv_enviar.raiz:
                self._atualizar_contagem_enviar()

    def _salvar_cfg(self):
        self.cfg.update({
            "link_origem": self.e_link.get().strip(),
            "pasta_saida": self.e_saida.get().strip(),
            "pasta_enviar": self.e_local.get().strip(),
            "link_destino": self.e_destino.get().strip(),
            "converter": bool(self.v_conv.get()),
        })
        core.salvar_config(self.cfg)

    # ---------- log e fila ----------

    def _escrever_log(self, aba, texto):
        caixa = self.ui[aba]["log"]
        caixa.configure(state="normal")
        caixa.insert("end", texto + "\n")
        caixa.see("end")
        caixa.configure(state="disabled")

    def _processar_fila(self):
        try:
            while True:
                item = self.fila.get_nowait()
                tipo = item[0]
                if tipo == "log":
                    self._escrever_log(item[1], item[2])
                elif tipo == "prog":
                    ui = self.ui[item[1]]
                    if item[3]:
                        ui["barra"].set(item[2] / item[3])
                    ui["rotulo"].configure(text=f"{item[2]} de {item[3]} arquivos")
                elif tipo == "fim":
                    self._fim(*item[1:])
                elif tipo == "conta":
                    item[1](item[2])
        except queue.Empty:
            pass
        self.after(80, self._processar_fila)

    def _tarefa(self, aba, alvo, depois=None):
        if self.ocupado:
            return
        self._salvar_cfg()
        self.cancelar.clear()
        self._set_ocupado(True)
        ui = self.ui[aba]
        ui["barra"].set(0)
        ui["rotulo"].configure(text="")
        ctx = core.Ctx(log=lambda m: self.fila.put(("log", aba, m)),
                       progresso=lambda a, b: self.fila.put(("prog", aba, a, b)),
                       cancelar=self.cancelar)

        def trabalhar():
            try:
                resultado = alvo(ctx)
                self.fila.put(("fim", aba, depois, resultado, None))
            except core.Cancelado:
                self.fila.put(("fim", aba, None, None, "Interrompido. Rode de novo para continuar de onde parou."))
            except Exception as e:  # noqa: BLE001
                self.fila.put(("fim", aba, None, None, core.motivo_erro(e) or type(e).__name__))

        threading.Thread(target=trabalhar, daemon=True).start()

    def _fim(self, aba, depois, resultado, erro):
        self._set_ocupado(False)
        if erro:
            self._escrever_log(aba, erro)
            return
        if depois:
            depois(resultado)

    # ---------- credenciais e contas ----------

    def _escolher_credenciais(self):
        arq = filedialog.askopenfilename(title="Escolha o credentials.json",
                                         filetypes=[("JSON", "*.json"), ("Todos", "*.*")])
        if not arq:
            return
        try:
            core.importar_credenciais(arq)
        except ValueError as e:
            messagebox.showerror("Drive Clone", str(e))
            return
        self._atualizar_credenciais()
        messagebox.showinfo("Drive Clone", "Credenciais salvas. Agora clique em "
                            "\"Entrar / trocar\" para conectar a conta de origem.")

    def _login_silencioso(self):
        """Se já há login salvo, mostra a conta sem abrir o navegador."""
        def tentar(quem, escopo, rotulo, atribuir):
            token = core.caminho_token(quem)
            if not token.exists():
                return
            try:
                api = core.autenticar(token, escopo, core.caminho_credenciais(),
                                      interativo=False)
                email = api.usuario()
            except Exception:
                return
            self.fila.put(("conta", lambda e, a=api: (atribuir(a), self._status_conta(rotulo, e)), email))

        for quem, escopo, rot, setter in (
                ("origem", core.SCOPE_LEITURA, self.st_origem, lambda a: setattr(self, "api_origem", a)),
                ("destino", core.SCOPE_ARQUIVOS, self.st_destino, lambda a: setattr(self, "api_destino", a))):
            threading.Thread(target=tentar, args=(quem, escopo, rot, setter), daemon=True).start()
        self._status_conta(self.st_origem, None)
        self._status_conta(self.st_destino, None)
        self.l_conta_dest.configure(text="Conta de destino: não conectada")

    def _entrar_origem(self):
        def alvo(ctx):
            api = core.autenticar(core.caminho_token("origem"), core.SCOPE_LEITURA,
                                  core.caminho_credenciais(), ctx, forcar=True)
            return api, api.usuario()

        def depois(res):
            self.api_origem = res[0]
            self._status_conta(self.st_origem, res[1])
            self._escrever_log("Baixar", "Conta de origem conectada: " + res[1])
            messagebox.showinfo("Drive Clone", "Conta de origem conectada: " + res[1])
        self._tarefa("Baixar", alvo, depois)

    def _escopo_destino(self):
        return core.SCOPE_TOTAL if self.e_destino.get().strip() else core.SCOPE_ARQUIVOS

    def _entrar_destino(self):
        escopo = self._escopo_destino()

        def alvo(ctx):
            api = core.autenticar(core.caminho_token("destino"), escopo,
                                  core.caminho_credenciais(), ctx, forcar=True)
            return api, api.usuario()

        def depois(res):
            self.api_destino = res[0]
            self._status_conta(self.st_destino, res[1])
            self.l_conta_dest.configure(text="Conta de destino: " + res[1])
            self._escrever_log("Enviar", "Conta de destino conectada: " + res[1])
            messagebox.showinfo("Drive Clone", "Conta de destino conectada: " + res[1])
        self._tarefa("Enviar", alvo, depois)

    # ---------- baixar ----------

    def _escolher_saida(self):
        pasta = filedialog.askdirectory(title="Onde salvar")
        if pasta:
            self.e_saida.delete(0, "end")
            self.e_saida.insert(0, pasta)

    def _mapear_origem(self):
        link = self.e_link.get().strip()
        if not link:
            messagebox.showwarning("Drive Clone", "Cole o link ou o ID da pasta.")
            return
        try:
            pasta_id = core.extrair_id(link)
        except ValueError as e:
            messagebox.showwarning("Drive Clone", str(e))
            return

        def alvo(ctx):
            api = self.api_origem or core.autenticar(
                core.caminho_token("origem"), core.SCOPE_LEITURA, core.caminho_credenciais(), ctx)
            meta = api.info(pasta_id)
            if meta.get("mimeType") != core.FOLDER:
                raise ValueError("O link informado não é de uma pasta.")
            ctx.log(f"Pasta: {meta['name']}")
            return api, core.mapear(api, pasta_id, meta["name"], ctx)

        def depois(res):
            self.api_origem, self.raiz_baixar = res
            self.arv_baixar.carregar(self.raiz_baixar)

        self._tarefa("Baixar", alvo, depois)

    def _baixar(self):
        if not self.raiz_baixar:
            return
        destino = self.e_saida.get().strip()
        if not destino:
            messagebox.showwarning("Drive Clone", "Escolha onde salvar.")
            return
        sel = self.arv_baixar.marcados()
        api, raiz = self.api_origem, self.raiz_baixar

        def alvo(ctx):
            return core.baixar(api, raiz, Path(destino), sel, ctx)

        def depois(res):
            linhas, base = res
            r = core.resumo(linhas)
            self._escrever_log("Baixar", "Concluído: " + ", ".join(f"{k}: {v}" for k, v in sorted(r.items())))
            self._escrever_log("Baixar", f"Arquivos em: {base}")
            pendentes = core.falhas(linhas)
            if pendentes:
                self._escrever_log("Baixar", f"Para baixar manualmente ({len(pendentes)}), "
                                             "lista também em _para_baixar_manualmente.txt:")
                for l in pendentes[:60]:
                    self._escrever_log("Baixar", f"  [{l['status']}] {l['caminho']}"
                                                 + (f" ({l['detalhe']})" if l["detalhe"] else ""))
                    self._escrever_log("Baixar", f"      {l['link']}")
                if len(pendentes) > 60:
                    self._escrever_log("Baixar", f"  ... e mais {len(pendentes) - 60}; veja o arquivo.")
            self.ultima_saida = str(base).replace("\\\\?\\", "")
            self.b_abrir.configure(state="normal")
            self.e_local.delete(0, "end")
            self.e_local.insert(0, self.ultima_saida)
            self._salvar_cfg()

        self._tarefa("Baixar", alvo, depois)

    # ---------- enviar ----------

    def _escolher_local(self):
        pasta = filedialog.askdirectory(title="Pasta a enviar")
        if pasta:
            self.e_local.delete(0, "end")
            self.e_local.insert(0, pasta)

    def _carregar_local(self):
        pasta = self.e_local.get().strip()
        if not pasta or not Path(pasta).is_dir():
            messagebox.showwarning("Drive Clone", "Escolha uma pasta que exista no computador.")
            return

        def alvo(ctx):
            return core.mapear_local(pasta)

        def depois(raiz):
            self.raiz_enviar = raiz
            self.arv_enviar.carregar(raiz)
            self._escrever_log("Enviar", f"Pasta carregada: {raiz.nome}")

        self._tarefa("Enviar", alvo, depois)

    def _enviar(self, simular):
        if not self.raiz_enviar:
            return
        link = self.e_destino.get().strip()
        try:
            pai = core.extrair_id(link) if link else "root"
        except ValueError as e:
            messagebox.showwarning("Drive Clone", "Pasta de destino: " + str(e))
            return
        nome = self.e_nome.get().strip() or self.raiz_enviar.nome
        sel = self.arv_enviar.marcados()
        raiz, converter, escopo = self.raiz_enviar, bool(self.v_conv.get()), self._escopo_destino()

        def alvo(ctx):
            if simular:
                return core.enviar(None, raiz, sel, pai, nome, converter, ctx, simular=True)
            api = self.api_destino
            if api is None:
                api = core.autenticar(core.caminho_token("destino"), escopo,
                                      core.caminho_credenciais(), ctx)
            return core.enviar(api, raiz, sel, pai, nome, converter, ctx)

        def depois(res):
            self._escrever_log("Enviar", f"{'Simulação' if simular else 'Concluído'}: "
                               f"{res['enviados']} arquivos, {res['pulados']} já enviados antes, "
                               f"{res['erros']} erros.")

        self._tarefa("Enviar", alvo, depois)

    def _fechar(self):
        self.cancelar.set()
        self._salvar_cfg()
        self.destroy()


def main():
    App().mainloop()
