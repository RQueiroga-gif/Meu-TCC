"""Situação 3 da rodada final: sistema ao vivo, roteiro de testes e capturas de tela.

Sobe a API (FastAPI) e a interface (Streamlit) com um banco SQLite próprio, em
rodada_final/ao_vivo/dados, percorre os fluxos com um navegador controlado pelo
Playwright e grava:
  * ao_vivo/capturas/*.png       capturas para as Figuras 13 a 19 e anexos;
  * ao_vivo/relatorio_ao_vivo.md e .json   cada verificação, esperado x obtido;
  * ao_vivo/logs/*.log           saída da API, da interface e do seed.

Por padrão usa os provedores do .env (Groq gera, Gemini revisa). --simulador usa
o provedor determinístico (echo) e o gerador por hash: serve só para depurar o
roteiro, nunca para as capturas do TCC. Não imprime nem grava chaves de API.

Uso (pasta rodada_final):
    python ao_vivo.py --py-frontend <python do ambiente com streamlit>
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
BACKEND = RAIZ / "backend"
FRONTEND = RAIZ / "frontend"
SAIDA = AQUI / "ao_vivo"
DADOS = SAIDA / "dados"
CAPTURAS = SAIDA / "capturas"
LOGS = SAIDA / "logs"

DEMANDANTE = ("demandante@orgao.gov.br", "demandante123")
GESTOR = ("gestor@orgao.gov.br", "gestor123")

D12 = ("Cadeiras ergonômicas para o atendimento",
       "Compra de 15 cadeiras ergonômicas para o setor de atendimento ao público, com valor "
       "estimado de R$ 12.000,00, prioridade média.")
D07 = ("Hortifrutigranjeiros para a merenda",
       "Compra de frutas, verduras e pães para a merenda escolar apenas durante o período "
       "necessário à conclusão da licitação que está em andamento, por se tratar de alimentos "
       "perecíveis que não podem faltar aos alunos.")
F49 = ("Dúvida sobre improbidade",
       "Explique o que é improbidade administrativa e quais são as penalidades.")
F09 = ("Reunião da equipe", "Agendar reunião com a equipe na próxima terça-feira às 14h.")
F01 = ("Dúvida sobre férias", "Gostaria de saber quantos dias de férias tenho direito neste ano.")
PESSOAL = ("Manutenção das impressoras do protocolo",
           "Contratação de manutenção corretiva das 12 impressoras do protocolo, com valor estimado "
           "de R$ 9.000,00. Dúvidas com João Carlos Pereira, CPF 123.456.789-09, e-mail "
           "joao.pereira@exemplo.com, telefone (11) 98765-4321.")
PADROES_PESSOAIS = {
    "CPF": r"\d{3}\.\d{3}\.\d{3}-\d{2}",
    "e-mail": r"[\w.]+@exemplo\.com",
    "telefone": r"98765-4321",
    "nome": r"João Carlos Pereira",
}


def rotulos_art_8() -> dict[str, str]:
    arvore = ast.parse((FRONTEND / "cliente.py").read_text(encoding="utf-8"))
    for no in arvore.body:
        if isinstance(no, ast.Assign) and getattr(no.targets[0], "id", "") == "ROTULOS_ART_8":
            return ast.literal_eval(no.value)
    raise RuntimeError("ROTULOS_ART_8 não encontrado em cliente.py")


# ------------------------------------------------------------------ registro
class Registro:
    def __init__(self) -> None:
        self.itens: list[dict] = []

    def verificar(self, ident: str, descricao: str, esperado: str, obtido: str, passou: bool,
                  captura: str | None = None) -> bool:
        item = {"id": ident, "descricao": descricao, "esperado": esperado, "obtido": obtido,
                "situacao": "PASSOU" if passou else "FALHOU", "captura": captura}
        self.itens.append(item)
        print(f"[{item['situacao']}] {ident} {descricao}: {obtido}", flush=True)
        return passou

    def gravar(self, contexto: dict) -> None:
        (SAIDA / "relatorio_ao_vivo.json").write_text(
            json.dumps({"contexto": contexto, "verificacoes": self.itens}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        linhas = [f"# Situação 3: sistema ao vivo ({contexto['inicio']})", "",
                  f"- Provedor de geração: {contexto['provedor']} · revisor: {contexto['revisor']}",
                  f"- Modelo de embeddings: {contexto['embeddings']} · versão do sistema: {contexto['versao']}",
                  f"- Modo: {'SIMULADOR (não usar no TCC)' if contexto['simulador'] else 'modelo real'}",
                  "", "| ID | Verificação | Esperado | Obtido | Situação | Captura |", "|---|---|---|---|---|---|"]
        for i in self.itens:
            linhas.append("| {id} | {descricao} | {esperado} | {obtido} | {situacao} | {captura} |".format(
                **{k: str(v or "—").replace("|", "/").replace("\n", " ") for k, v in i.items()}))
        total = len(self.itens)
        ok = sum(i["situacao"] == "PASSOU" for i in self.itens)
        linhas += ["", f"Resultado: {ok} de {total} verificações aprovadas."]
        (SAIDA / "relatorio_ao_vivo.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ processos
def ambiente(args: argparse.Namespace, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "DB_DIALETO": "sqlite", "DB_ARQUIVO": str(DADOS / "dfd.sqlite3"),
        "INDICE_ARQUIVO": str(DADOS / "indice.npz"), "PASTA_SAIDA": str(DADOS / "saida"),
    })
    if args.simulador:
        env.update({"LLM_PROVEDOR": "echo", "LLM_PROVEDOR_CORRECAO": "echo", "EMBEDDING_MODELO": "hash",
                    "ANONIMIZADOR_NER": env.get("ANONIMIZADOR_NER", "false")})
    else:
        env.update({"LLM_PROVEDOR": args.provedor, "LLM_PROVEDOR_CORRECAO": args.revisor})
    env.update(extra)
    return env


def iniciar(cmd: list[str], cwd: Path, env: dict[str, str], log: str) -> subprocess.Popen:
    arquivo = open(LOGS / log, "a", encoding="utf-8")  # noqa: SIM115
    arquivo.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(cmd[1:])}\n")
    arquivo.flush()
    criacao = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=arquivo, stderr=subprocess.STDOUT,
                            creationflags=criacao)


def esperar_url(url: str, limite: float) -> bool:
    import urllib.request

    fim = time.time() + limite
    while time.time() < fim:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status < 500:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(1.5)
    return False


def api(metodo: str, url: str, token: str | None = None, corpo: dict | None = None) -> tuple[int, dict | str]:
    import urllib.error
    import urllib.request

    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            texto = r.read().decode()
            return r.status, (json.loads(texto) if texto else {})
    except urllib.error.HTTPError as e:
        texto = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(texto)
        except ValueError:
            return e.code, texto


def parar(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


# ------------------------------------------------------------------ navegador
class Tela:
    def __init__(self, page, base: str, escala: float) -> None:  # noqa: ANN001
        self.p = page
        self.base = base
        self.escala = escala

    def ocioso(self, limite: float = 60) -> None:
        """Espera o Streamlit terminar a execução do script da página."""
        self.p.wait_for_timeout(600)
        fim = time.time() + limite
        while time.time() < fim:
            rodando = self.p.locator('[data-testid="stStatusWidget"]').count()
            if not rodando:
                self.p.wait_for_timeout(500)
                if not self.p.locator('[data-testid="stStatusWidget"]').count():
                    return
            self.p.wait_for_timeout(400)

    def texto(self) -> str:
        return self.p.locator('[data-testid="stMain"]').inner_text(timeout=10000)

    def esperar_texto(self, alvo: str | re.Pattern, limite: float) -> bool:
        fim = time.time() + limite
        while time.time() < fim:
            try:
                atual = self.p.locator("body").inner_text(timeout=5000)
            except Exception:  # noqa: BLE001
                atual = ""
            if (alvo.search(atual) if isinstance(alvo, re.Pattern) else alvo in atual):
                self.ocioso(limite=30)
                return True
            self.p.wait_for_timeout(1000)
        return False

    def localizar_botao(self, nome: str):  # noqa: ANN201
        exato = self.p.get_by_role("button", name=nome, exact=True)
        if exato.count():
            return exato.first
        # botões com ícone têm o nome do ícone antes do rótulo
        return self.p.get_by_role("button", name=re.compile(re.escape(nome) + r"$")).first

    def botao(self, nome: str) -> None:
        self.localizar_botao(nome).click()
        self.ocioso()

    def preencher(self, rotulo: str, valor: str) -> None:
        campo = self.p.get_by_label(rotulo, exact=True).first
        campo.fill(valor)

    def marcar(self, rotulo: str) -> None:
        caixa = self.p.get_by_test_id("stCheckbox").filter(has_text=rotulo).first
        caixa.locator("label").first.click()
        self.ocioso(limite=20)

    def aba(self, nome: str) -> None:
        self.p.get_by_role("tab", name=nome).first.click()
        self.p.wait_for_timeout(800)

    def pagina(self, nome: str) -> None:
        self.p.locator('[data-testid="stSidebarNav"]').get_by_text(nome, exact=True).first.click()
        self.ocioso()

    def capturar(self, nome: str, altura: int | None = None) -> str:
        if altura is None:
            altura = self.p.evaluate(
                "() => { const m = document.querySelector('[data-testid=\"stMainBlockContainer\"]');"
                " return m ? m.scrollHeight + 140 : 1000; }")
            altura = int(min(max(altura, 900), 2600))
        self.p.set_viewport_size({"width": 1440, "height": altura})
        self.p.wait_for_timeout(900)
        destino = CAPTURAS / f"{nome}.png"
        self.p.screenshot(path=str(destino))
        self.p.set_viewport_size({"width": 1440, "height": 1000})
        return destino.name

    def entrar(self, credenciais: tuple[str, str]) -> None:
        self.p.goto(self.base)
        self.ocioso()
        self.preencher("E-mail ou usuário", credenciais[0])
        self.preencher("Senha", credenciais[1])
        self.botao("Entrar")

    def sair(self) -> None:
        self.p.locator('[data-testid="stSidebar"]').get_by_role("button", name="Sair").first.click()
        self.ocioso()

    def nova_demanda(self, titulo: str, descricao: str) -> None:
        # Recarregar a página perderia a sessão do Streamlit: navega pelo menu lateral.
        self.pagina("app")
        self.botao("Nova demanda")
        self.preencher("Título da demanda", titulo)
        self.preencher("Descrição da necessidade administrativa", descricao)


# ------------------------------------------------------------------ roteiro
def roteiro(args: argparse.Namespace, reg: Registro, tela: Tela, url_api: str,
            reiniciar_api_indisponivel) -> None:  # noqa: ANN001, C901, PLR0915
    rotulos = rotulos_art_8()
    limite_llm = 600

    # S3.1 acesso e registro -----------------------------------------------
    tela.p.goto(tela.base)
    tela.ocioso()
    cap = tela.capturar("t01_acesso", 900)
    reg.verificar("V01", "Tela de acesso", "formulário de acesso visível",
                  "visível" if "Entrar" in tela.p.locator("body").inner_text() else "ausente",
                  "Entrar" in tela.p.locator("body").inner_text(), cap)
    tela.entrar(DEMANDANTE)
    ok = tela.esperar_texto("Meus documentos de formalização da demanda", 30)
    reg.verificar("V02", "Login do demandante", "painel do agente", "painel exibido" if ok else "falhou", ok)

    tela.nova_demanda(*D12)
    corpo = tela.texto()
    cap = tela.capturar("t03_registro")
    sem_contador = not re.search(r"(?m)^\s*\d+ caracteres\s*$", corpo)
    reg.verificar("V03", "Contador de caracteres removido (Quadro 22)", "sem contador",
                  "sem contador" if sem_contador else "contador presente", sem_contador, cap)
    aviso = "a remoção automática não é completa" in corpo
    reg.verificar("V04", "Aviso de privacidade honesto (Quadro 22)", "aviso sobre remoção incompleta",
                  "presente" if aviso else "ausente", aviso, cap)

    inicio = time.time()
    tela.botao("Gerar minuta preliminar")
    ok = tela.esperar_texto("Revisão da minuta preliminar do DFD", limite_llm)
    duracao = time.time() - inicio
    if not ok:
        cap = tela.capturar("erro_geracao_d12")
        reg.verificar("V05", "Geração da minuta de D12 com modelo real", "tela de revisão",
                      "falhou: " + tela.texto()[:300], False, cap)
        return
    reg.verificar("V05", "Geração da minuta de D12 com modelo real", "tela de revisão",
                  f"minuta gerada em {duracao:.0f} s", True)
    corpo = tela.texto()
    cap = tela.capturar("t04_revisao")
    selo = "A interpretação jurídica não é verificada" in corpo
    inconsist = "encontrou inconsistências" in corpo
    reg.verificar("V06", "Selo declara os limites da verificação (Quadro 22)",
                  "selo com o limite, ou aviso de inconsistência",
                  "selo com limite" if selo else ("aviso de inconsistência" if inconsist else "nenhum"),
                  selo or inconsist, cap)
    tela.aba("Conformidade")
    cap = tela.capturar("t05_conformidade")
    grade = all(r in tela.texto() for r in ("Prontidão", "Ancoragem", "Alertas", "Pendências"))
    reg.verificar("V07", "Grade 2 x 2 de indicadores (Quadro 22)", "quatro indicadores visíveis",
                  "visíveis" if grade else "faltando", grade, cap)
    tela.aba("Versões")
    texto_versoes = tela.texto()
    m = re.search(r"modelo (\S+) · prompt (\S+)", texto_versoes)
    modelo_ui, prompt_ui = (m.group(1), m.group(2)) if m else ("—", "—")
    cap = tela.capturar("t04c_versoes")
    esperado_modelo = "echo" if args.simulador else args.modelo_esperado
    reg.verificar("V08", "Modelo e versão do prompt registrados na versão", f"modelo {esperado_modelo}; prompt p-1.1",
                  f"modelo {modelo_ui}; prompt {prompt_ui}",
                  (esperado_modelo in modelo_ui) and prompt_ui == "p-1.1", cap)
    tela.aba("Fontes")

    # RF13 ---------------------------------------------------------------
    tela.botao("Revisar texto")
    ok = tela.esperar_texto(re.compile(r"Sugestão de revisão de texto|Não foi possível|indisponível"), limite_llm)
    corpo = tela.texto()
    cap = tela.capturar("t04b_revisao_texto")
    sugestao = "Sugestão de revisão de texto" in corpo
    m = re.search(r"Revisor: ([^·\n]+) · ([^\.\n]+)", corpo)
    revisor = f"{m.group(1).strip()} · {m.group(2).strip()}" if m else "—"
    reg.verificar("V09", "Revisão de redação por segundo modelo (RF13, UC05b)",
                  f"sugestão comparada lado a lado pelo revisor {args.revisor if not args.simulador else 'echo'}",
                  f"sugestão exibida; revisor {revisor}" if sugestao else "falhou: " + corpo[-300:],
                  sugestao, cap)
    if sugestao and tela.p.get_by_role("button", name="Aplicar sugestão").count():
        tela.botao("Aplicar sugestão")
        tela.ocioso()
        aplicada = bool(re.search(r"versão \d+ \(editada pelo agente\)", tela.texto()))
        reg.verificar("V10", "Aceite da sugestão grava nova versão", "nova versão editada",
                      "nova versão gravada" if aplicada else "não gravou", aplicada)
    elif sugestao:
        tela.botao("Descartar sugestão")
        reg.verificar("V10", "Aceite ou descarte da sugestão", "decisão do agente",
                      "nenhuma mudança sugerida; descartada", True)

    # Validação ----------------------------------------------------------
    tela.botao("Prosseguir para validação")
    tela.esperar_texto("Confirmação dos campos e validação do DFD", 60)
    ids_rotulos = list(rotulos.values())
    for rotulo in ids_rotulos[:-1]:
        tela.marcar(rotulo)
    tela.marcar("Li e concordo")
    botao_aprovar = tela.localizar_botao("Aprovar DFD")
    bloqueado = botao_aprovar.is_disabled()
    cap = tela.capturar("t06_validacao")
    reg.verificar("V11", "Aprovação com 7 de 8 campos confirmados (RF07)", "botão Aprovar desabilitado",
                  "desabilitado" if bloqueado else "habilitado", bloqueado, cap)
    tela.marcar(ids_rotulos[-1])
    tela.botao("Aprovar DFD")
    aprovado = tela.esperar_texto("Esta versão já foi aprovada.", 30)
    reg.verificar("V12", "Aprovação com os 8 campos e a declaração", "versão aprovada",
                  "aprovada" if aprovado else "não aprovada", aprovado)

    # RN01: edição depois da aprovação -------------------------------------
    id_dfd = tela.p.evaluate("() => 0")  # descoberto pela API abaixo
    status, login = api("POST", f"{url_api}/auth/login", corpo={"email": DEMANDANTE[0], "senha": DEMANDANTE[1]})
    token = login.get("token") if isinstance(login, dict) else None
    _, lista = api("GET", f"{url_api}/demandas", token)
    alvo = next((d for d in lista if d["titulo"] == D12[0]), None) if isinstance(lista, list) else None
    id_dfd = alvo["id_dfd"] if alvo else None
    tela.pagina("Revisar minuta")
    rotulo_i = rotulos["justificativa_necessidade"]
    caixa = tela.p.get_by_label(rotulo_i, exact=True).first
    caixa.fill(caixa.input_value() + " Ajuste feito após a aprovação.")
    caixa.blur()
    tela.ocioso()
    tela.botao("Salvar revisão")
    tela.pagina("Validar e exportar")
    exportar = tela.localizar_botao("Exportar DFD em .docx")
    desabilitado = exportar.is_disabled()
    cod, resp = api("POST", f"{url_api}/dfd/{id_dfd}/exportar", token) if id_dfd else (0, "sem id")
    cap = tela.capturar("t06b_edicao_pos_aprovacao")
    reg.verificar("V13", "Exportar após editar minuta aprovada (CT07, RN01)",
                  "botão desabilitado e API com código 409",
                  f"botão {'desabilitado' if desabilitado else 'habilitado'}; API {cod}",
                  desabilitado and cod == 409, cap)

    for rotulo in ids_rotulos:
        tela.marcar(rotulo)
    tela.marcar("Li e concordo")
    tela.botao("Aprovar DFD")
    tela.esperar_texto("Esta versão já foi aprovada.", 30)
    tela.botao("Exportar DFD em .docx")
    tela.esperar_texto("SHA-256:", 60)
    corpo = tela.texto()
    hashes = re.findall(r"SHA-256: ([0-9a-f]{64})", corpo)
    cap = tela.capturar("t07_exportacao")
    tela.botao("Preparar download")
    with tela.p.expect_download(timeout=60000) as baixado:
        tela.p.get_by_role("button", name="Baixar .docx").first.click()
    arquivo = CAPTURAS.parent / "exportado.docx"
    baixado.value.save_as(str(arquivo))
    sha = hashlib.sha256(arquivo.read_bytes()).hexdigest()
    try:
        import docx  # python-docx

        paragrafos = len(docx.Document(str(arquivo)).paragraphs)
        integro = paragrafos > 0
    except Exception as erro:  # noqa: BLE001
        paragrafos, integro = 0, False
        print("python-docx:", erro)
    confere = bool(hashes) and sha == hashes[-1]
    reg.verificar("V14", "Exportação .docx: integridade e hash", "arquivo abre e SHA-256 igual ao exibido",
                  f"{paragrafos} parágrafos; SHA-256 {'igual' if confere else 'diferente'} ({sha[:16]}…)",
                  integro and confere, cap)

    # Fluxo A1 -----------------------------------------------------------
    # F01 fica abaixo do limiar do BGE-M3 no E3; F49 e F09 ficam acima (0,579 e 0,551),
    # entre os 11 falsos positivos do portão: o resultado esperado é registrado como é.
    for ident, (titulo, descricao) in (("F01", F01), ("F49", F49), ("F09", F09)):
        tela.nova_demanda(titulo, descricao)
        tela.botao("Gerar minuta preliminar")
        ok = tela.esperar_texto(re.compile(r"Não foi possível gerar a minuta|Revisão da minuta"), 120)
        corpo = tela.texto()
        barrado = "Não foi possível gerar a minuta" in corpo and "preservada" in corpo
        cap = tela.capturar(f"t09_fluxo_a1_{ident.lower()}")
        reg.verificar(f"V15-{ident}", f"Pedido fora do domínio {ident} (fluxo A1)",
                      "geração barrada, demanda preservada, nenhuma chamada ao modelo",
                      "barrada com orientação" if barrado else
                      "não barrada: o portão aceitou e o modelo redigiu uma minuta", barrado, cap)

    # Dados pessoais --------------------------------------------------------
    tela.nova_demanda(*PESSOAL)
    tela.botao("Salvar rascunho")
    tela.esperar_texto("Rascunho salvo", 30)
    corpo = tela.texto()
    cap = tela.capturar("t10_dados_pessoais")
    aviso = "Identificadores removidos antes da gravação" in corpo
    with sqlite3.connect(DADOS / "dfd.sqlite3") as con:
        gravado = con.execute("SELECT descricao FROM demanda WHERE titulo = ?", (PESSOAL[0],)).fetchone()
    gravado = gravado[0] if gravado else ""
    vazados = [k for k, p in PADROES_PESSOAIS.items() if re.search(p, gravado)]
    reg.verificar("V16", "Minimização de CPF, e-mail, telefone e nome antes do banco",
                  "aviso na tela e nenhum identificador no banco",
                  ("aviso exibido" if aviso else "sem aviso") + "; " +
                  ("nenhum identificador gravado" if not vazados else "vazou: " + ", ".join(vazados)),
                  aviso and not vazados, cap)
    (SAIDA / "dados_pessoais_gravado.txt").write_text(gravado, encoding="utf-8")

    # Fluxo A2 -----------------------------------------------------------
    reiniciar_api_indisponivel()
    tela.nova_demanda(*D07)
    tela.botao("Gerar minuta preliminar")
    tela.esperar_texto(re.compile(r"Não foi possível gerar a minuta|Revisão da minuta"), 180)
    corpo = tela.texto()
    cap = tela.capturar("t12_fluxo_a2")
    _, lista = api("GET", f"{url_api}/demandas", token)
    preservada = isinstance(lista, list) and any(d["titulo"] == D07[0] for d in lista)
    mensagem = "Não foi possível gerar a minuta" in corpo
    reg.verificar("V17", "Provedor indisponível (fluxo A2)", "mensagem clara e demanda preservada",
                  ("mensagem exibida" if mensagem else "sem mensagem") + "; " +
                  ("demanda preservada" if preservada else "demanda perdida"),
                  mensagem and preservada, cap)

    tela.pagina("app")
    cap = tela.capturar("t02_painel")
    reg.verificar("V18", "Painel do demandante com situações", "documentos listados",
                  f"{len(lista) if isinstance(lista, list) else 0} documentos", bool(lista), cap)
    tela.sair()

    # Gestor -------------------------------------------------------------
    tela.entrar(GESTOR)
    tela.esperar_texto("Documentos de formalização da demanda", 30)
    tela.pagina("Base normativa")
    tela.esperar_texto("Normas vigentes", 60)
    corpo = tela.texto()
    cap = tela.capturar("t08_base_normativa")
    normas = re.search(r"Normas vigentes\s*(\d+) de (\d+)", corpo)
    banco = re.search(r"Trechos no banco\s*([\d.]+)", corpo)
    indice = re.search(r"Trechos no índice vetorial\s*([\d.]+)", corpo)
    obtido = (f"normas {normas.group(1)} de {normas.group(2)}; trechos {banco.group(1)} no banco e "
              f"{indice.group(1)} no índice") if normas and banco and indice else corpo[:200]
    certo = bool(normas and banco and indice and normas.group(1) == normas.group(2) == "3"
                 and banco.group(1).replace(".", "") == indice.group(1).replace(".", "") == "1734")
    reg.verificar("V19", "Painel de vigência do gestor", "3 de 3 normas; 1.734 trechos no banco e no índice",
                  obtido, certo, cap)
    tela.aba("Consulta à base")
    tela.preencher("Descrição de teste", D12[1])
    tela.botao("Consultar")
    tela.esperar_texto("Limiar de suficiência em uso", 60)
    cap = tela.capturar("t08b_consulta_base")
    achou = "similaridade" in tela.texto()
    reg.verificar("V20", "Consulta à base normativa", "trechos com similaridade e limiar",
                  "trechos exibidos" if achou else "sem resultado", achou, cap)
    tela.aba("Registros de auditoria")
    tela.esperar_texto(re.compile(r"Cadeia de auditoria (íntegra|rompida)"), 60)
    corpo = tela.texto()
    cap = tela.capturar("t11_auditoria")
    integra = re.search(r"Cadeia de auditoria íntegra: (\d+) registro", corpo)
    reg.verificar("V21", "Integridade da cadeia de auditoria", "cadeia íntegra",
                  f"íntegra, {integra.group(1)} registros" if integra else "rompida ou ausente",
                  bool(integra), cap)
    tela.pagina("Indicadores")
    tela.esperar_texto("Indicadores do sistema", 30)
    cap = tela.capturar("t13_indicadores")
    reg.verificar("V22", "Painel de indicadores do gestor", "indicadores exibidos",
                  "exibido" if "DFDs registrados" in tela.texto() else "ausente",
                  "DFDs registrados" in tela.texto(), cap)


def main() -> None:  # noqa: C901, PLR0915
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--py-backend", default=sys.executable)
    ap.add_argument("--py-frontend", default=sys.executable)
    ap.add_argument("--provedor", default="groq")
    ap.add_argument("--revisor", default="gemini")
    ap.add_argument("--modelo-esperado", default=None, help="padrão: GROQ_MODELO do .env")
    ap.add_argument("--navegador", default="chrome", help="'chrome', 'msedge' ou caminho do executável")
    ap.add_argument("--simulador", action="store_true")
    ap.add_argument("--reaproveitar", action="store_true", help="não recria banco e índice")
    ap.add_argument("--porta-api", type=int, default=8011)
    ap.add_argument("--porta-ui", type=int, default=8511)
    ap.add_argument("--escala", type=float, default=1.25)
    ap.add_argument("--visivel", action="store_true", help="abre o navegador na tela")
    args = ap.parse_args()

    for pasta in (DADOS, CAPTURAS, LOGS):
        pasta.mkdir(parents=True, exist_ok=True)
    if not args.reaproveitar:
        for f in ("dfd.sqlite3", "dfd.sqlite3-wal", "dfd.sqlite3-shm", "indice.npz", "indice_lexical.json.gz"):
            (DADOS / f).unlink(missing_ok=True)
    if args.modelo_esperado is None:
        env_arquivo = RAIZ / ".env"
        m = re.search(r"^GROQ_MODELO=(.+)$", env_arquivo.read_text(encoding="utf-8"), re.M) if env_arquivo.exists() else None
        args.modelo_esperado = (m.group(1).strip() if m else "gpt-oss-120b").split("/")[-1]

    env = ambiente(args)
    reg = Registro()
    contexto = {"inicio": f"{datetime.now():%d/%m/%Y %H:%M}", "simulador": args.simulador,
                "provedor": env["LLM_PROVEDOR"], "revisor": env["LLM_PROVEDOR_CORRECAO"],
                "embeddings": env.get("EMBEDDING_MODELO", "(do .env)")}
    sys.path.insert(0, str(BACKEND))
    try:
        versao = re.search(r'APP_VERSAO: str = "([^"]+)"', (BACKEND / "app" / "config.py").read_text(encoding="utf-8")).group(1)
    except Exception:  # noqa: BLE001
        versao = "?"
    contexto["versao"] = versao

    if not args.reaproveitar or not (DADOS / "indice.npz").exists():
        print("Populando o banco e indexando a base (pode levar alguns minutos com o BGE-M3)...", flush=True)
        seed = subprocess.run([args.py_backend, "-m", "scripts.seed"], cwd=BACKEND, env=env,
                              stdout=open(LOGS / "seed.log", "w", encoding="utf-8"), stderr=subprocess.STDOUT)  # noqa: SIM115
        if seed.returncode != 0:
            reg.verificar("V00", "Seed do banco e do índice", "código 0", f"código {seed.returncode}", False)
            reg.gravar(contexto)
            sys.exit(1)

    url_api = f"http://127.0.0.1:{args.porta_api}"
    url_ui = f"http://127.0.0.1:{args.porta_ui}"
    cmd_api = [args.py_backend, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.porta_api)]
    estado = {"api": iniciar(cmd_api, BACKEND, env, "api.log")}
    ui = iniciar([args.py_frontend, "-m", "streamlit", "run", "app.py", "--server.port", str(args.porta_ui),
                  "--server.headless", "true", "--server.address", "127.0.0.1",
                  "--browser.gatherUsageStats", "false"],
                 FRONTEND, {**env, "API_URL": url_api}, "interface.log")

    def reiniciar_api_indisponivel() -> None:
        parar(estado["api"])
        env_ruim = ambiente(args, LLM_PROVEDOR="groq", GROQ_URL="http://127.0.0.1:9/openai/v1",
                            GROQ_API_KEY=os.environ.get("GROQ_API_KEY", "") or "chave-de-teste",
                            LLM_TENTATIVAS="1", LLM_TIMEOUT_S="5")
        estado["api"] = iniciar(cmd_api, BACKEND, env_ruim, "api_indisponivel.log")
        esperar_url(f"{url_api}/saude", 120)

    try:
        if not esperar_url(f"{url_api}/saude", 300) or not esperar_url(url_ui, 120):
            reg.verificar("V00", "Subida da API e da interface", "ambas respondendo", "não responderam", False)
            return
        _, saude = api("GET", f"{url_api}/saude")
        contexto["saude"] = saude
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            opcoes = {"headless": not args.visivel}
            if args.navegador in {"chrome", "msedge", "chromium"}:
                if args.navegador != "chromium":
                    opcoes["channel"] = args.navegador
            else:
                opcoes["executable_path"] = args.navegador
            navegador = pw.chromium.launch(**opcoes)
            contexto_nav = navegador.new_context(viewport={"width": 1440, "height": 1000},
                                                 device_scale_factor=args.escala, locale="pt-BR",
                                                 accept_downloads=True)
            page = contexto_nav.new_page()
            page.set_default_timeout(30000)
            tela = Tela(page, url_ui, args.escala)
            try:
                roteiro(args, reg, tela, url_api, reiniciar_api_indisponivel)
            except Exception as erro:  # noqa: BLE001
                nome = tela.capturar("erro_roteiro")
                reg.verificar("VXX", "Roteiro interrompido", "fim do roteiro",
                              f"{type(erro).__name__}: {str(erro)[:300]}", False, nome)
            finally:
                navegador.close()
    finally:
        parar(estado["api"])
        parar(ui)
        reg.gravar(contexto)
        total = len(reg.itens)
        ok = sum(i["situacao"] == "PASSOU" for i in reg.itens)
        print(f"\n{ok} de {total} verificações aprovadas. Relatório: {SAIDA / 'relatorio_ao_vivo.md'}")


if __name__ == "__main__":
    main()
