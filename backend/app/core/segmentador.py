"""Segmentacao de atos normativos por dispositivo (Fase F1).

A unidade de recuperacao e o dispositivo — caput, paragrafo ou inciso, com as
alineas anexadas —, e nao um bloco de tamanho fixo. Essa escolha e a contribuicao
metodologica do trabalho: o dispositivo e a unidade de citacao juridica, de modo
que a recuperacao devolve a referencia pronta e verificavel (RN02).

Regras aplicadas:
  * conteudo tachado e descartado — convencao do Planalto para texto revogado ou
    alterado, marcada ora por tag (<strike>, <s>, <del>), ora por estilo CSS
    (text-decoration: line-through). A segunda forma foi identificada na
    avaliacao com o HTML integral (26/10/2026): sem trata-la, redacoes
    superadas da LGPD e da Lei nº 14.133/2021 entravam no indice como vigentes;
  * dispositivos "(VETADO)" e "(Revogado)" sao descartados (RN03);
  * anotacoes editoriais ("Incluido pela Lei...", "Vide...") sao removidas;
  * o caput do artigo (e o paragrafo, quando houver) prefixa o inciso, para que
    um inciso isolado nao perca o sentido no momento da vetorizacao;
  * texto de outra norma transcrito entre aspas (por exemplo, os arts. 337-E a
    337-P do Codigo Penal, inseridos pelo art. 178 da Lei nº 14.133/2021) nao e
    atribuido a norma que o transcreve.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

ANOTACAO = re.compile(
    r"\((?:Inclu[íi]d[oa]|Reda[çc][ãa]o dada|Vide|Vig[êe]ncia|Promulga[çc][ãa]o"
    r"|Regulamento|Produ[çc][ãa]o de efeitos?|Renumerad[oa])[^)]*\)",
    re.I,
)
RE_ART = re.compile(r"^Art\.\s*(\d+(?:-[A-Z])?)\s*(?:º|°|o)?\.?\s*[-–]?\s*(.*)", re.S)
# "§ 3º-A" e "XVI-A": dispositivos acrescidos por lei posterior recebem letra.
RE_PAR = re.compile(r"^§\s*(\d+)\s*(?:º|°|o)?(?:\s*-\s*([A-Z])\b)?\.?\s*(.*)", re.S)
RE_PAR_UNICO = re.compile(r"^Par[áa]grafo\s+[úu]nico\.?\s*(.*)", re.S | re.I)
RE_INC = re.compile(r"^([IVXLC]+(?:-[A-Z])?)\s*[-–—]\s*(.*)", re.S)
RE_ALI = re.compile(r"^([a-z])\)\s*(.*)", re.S)
DESCARTAR = re.compile(r"^(?:\d+\.\s*)?\(?\s*(VETADO|Revogad[oa])", re.I)
# Transcricao de outra norma (artigos que alteram leis): o bloco abre com aspas
# no inicio da linha e fecha com aspas no fim, seguidas ou nao de "(NR)".
ABRE_ASPAS = ("“", '"')
RE_FECHA_ASPAS = re.compile(r"[”\"]\s*(?:\(\s*NR\s*\))?\s*[;.]?\s*$")

RE_TACHADO_CSS = re.compile(r"line-through", re.I)
# Rotulos de links editoriais que sobram apos o fim do dispositivo
# ("...grande vulto; Vigência Vigência", "...hipóteses de contratação: Regulamento").
RE_ROTULO_FINAL = re.compile(r"([;.:]|\be)\s*(?:(?:Vig[êe]ncia|Regulamento)\s*)+$", re.I)
RE_ESPACO_PONTUACAO = re.compile(r"\s+([,.;:)])")
RE_NUMERO_PARTIDO = re.compile(r"(\d)\.\s+(\d{3})\b")   # "14. 133" -> "14.133"

TAMANHO_CONTEXTO = 400   # caracteres do caput mantidos como prefixo


def _analisador() -> str:
    """Prefere o html5lib, que reproduz o algoritmo de analise do HTML5.

    O HTML do Planalto tem aninhamento malformado (</font> fechando dentro de
    <p>). O lxml encerra o paragrafo antes da hora e trunca o dispositivo
    (caso real: Decreto nº 10.947/2022, art. 7º, III); o html5lib o reconstroi
    como um navegador. Sem a biblioteca, cai para o lxml.
    """
    try:
        import html5lib  # noqa: F401

        return "html5lib"
    except ImportError:  # pragma: no cover
        return "lxml"


@dataclass(slots=True)
class Dispositivo:
    """Unidade minima de recuperacao."""

    norma: str                      # "Lei nº 14.133/2021"
    referencia: str                 # "art. 18, § 1º, II"
    artigo: str
    texto: str
    contexto: str = ""              # caput (e paragrafo) que antecede o dispositivo
    alineas: list[str] = field(default_factory=list)

    @property
    def citacao(self) -> str:
        return f"{self.norma}, {self.referencia}"

    @property
    def texto_completo(self) -> str:
        """Texto do dispositivo com as alineas, sem o contexto."""
        if not self.alineas:
            return self.texto
        return self.texto + " " + " ".join(self.alineas)

    def texto_indexado(self) -> str:
        """Texto efetivamente vetorizado: citacao + contexto truncado + corpo."""
        prefixo = f"{self.citacao}."
        ctx = self.contexto[:TAMANHO_CONTEXTO].strip()
        corpo = self.texto_completo
        return f"{prefixo} {ctx} {corpo}".strip() if ctx else f"{prefixo} {corpo}"


SALTO_MAXIMO = 20   # salto de numeracao implausivel dentro de uma mesma norma


def _numero_base(numero: str) -> int:
    """'337-E' -> 337; '8' -> 8."""
    digitos = re.match(r"\d+", numero)
    return int(digitos.group()) if digitos else 0


def numerar_artigo(numero: str) -> str:
    """Aplica a numeracao ordinal ate o nono artigo (LC nº 95/1998, art. 10, I).

    >>> numerar_artigo("8"), numerar_artigo("6-A"), numerar_artigo("18")
    ('8º', '6º-A', '18')
    """
    base, _, sufixo = numero.partition("-")
    if base.isdigit() and int(base) < 10:
        return f"{base}º" + (f"-{sufixo}" if sufixo else "")
    return numero


def decodificar(bruto: bytes) -> str:
    """Decodifica o HTML oficial.

    As paginas do Planalto sao publicadas em Windows-1252; decodificar como UTF-8
    corrompe as acentuacoes ("formalizacao" vira "formaliza??o"). A funcao tenta
    UTF-8 primeiro e recorre a cp1252, que nunca falha.
    """
    for codec in ("utf-8", "cp1252"):
        try:
            return bruto.decode(codec)
        except UnicodeDecodeError:
            continue
    return bruto.decode("cp1252", errors="replace")


def linhas_limpas(html: str) -> list[str]:
    """Extrai os paragrafos uteis, ja sem texto tachado e sem anotacoes."""
    sopa = BeautifulSoup(html, _analisador())
    for tachado in sopa.find_all(["strike", "s", "del"]):
        tachado.decompose()
    for tachado in sopa.find_all(style=RE_TACHADO_CSS):
        tachado.decompose()

    linhas: list[str] = []
    for paragrafo in sopa.find_all("p"):
        texto = ANOTACAO.sub("", paragrafo.get_text(" ", strip=True))
        texto = re.sub(r"\s+", " ", texto).strip()
        texto = RE_NUMERO_PARTIDO.sub(r"\1.\2", RE_ESPACO_PONTUACAO.sub(r"\1", texto))
        texto = RE_ROTULO_FINAL.sub(r"\1", texto)
        if texto:
            linhas.append(texto)
    return linhas


def segmentar(html: str, norma: str) -> list[Dispositivo]:
    """Divide o ato normativo em dispositivos.

    Args:
        html: conteudo HTML ja decodificado.
        norma: citacao da norma, por exemplo "Lei nº 14.133/2021".

    Returns:
        Lista de dispositivos vigentes, na ordem do texto.
    """
    saida: list[Dispositivo] = []
    artigo: str | None = None
    ultimo_numero = 0                     # numero-base do ultimo artigo proprio
    transcricao = False                   # dentro de bloco citado de outra norma
    caput: str | None = None
    par_ref: str | None = None
    par_txt: str | None = None
    atual: Dispositivo | None = None      # dispositivo que recebe as proximas alineas

    for linha in linhas_limpas(html):
        # --------------------------------------------- transcricao (aspas)
        if transcricao:
            proprio = RE_ART.match(linha)
            if proprio and _numero_base(proprio.group(1)) in (ultimo_numero, ultimo_numero + 1):
                transcricao = False       # aspas sem fechamento: o proximo artigo encerra
            else:
                if RE_FECHA_ASPAS.search(linha):
                    transcricao = False
                continue
        if linha.startswith(ABRE_ASPAS):
            # Linha unica entre aspas ou inicio de bloco: em ambos os casos o
            # texto e da norma alterada, nao desta; alineas seguintes nao se
            # anexam ao dispositivo anterior.
            transcricao = not RE_FECHA_ASPAS.search(linha)
            atual = None
            continue

        # ---------------------------------------------------------- artigo
        if encontrado := RE_ART.match(linha):
            numero = _numero_base(encontrado.group(1))
            if ultimo_numero and numero > ultimo_numero + SALTO_MAXIMO:
                # Artigo fora da sequencia (ex.: "Art. 337-E" logo apos o 178):
                # e transcricao cujas aspas se perderam no HTML.
                transcricao, atual = True, None
                continue
            ultimo_numero = max(ultimo_numero, numero)
            artigo, caput = numerar_artigo(encontrado.group(1)), encontrado.group(2)
            par_ref = par_txt = None
            atual = None
            if caput and not DESCARTAR.match(caput):
                atual = Dispositivo(norma, f"art. {artigo}, caput", artigo, caput)
                saida.append(atual)       # o caput e o dispositivo corrente: alineas
            continue                      # ligadas a ele nao se perdem

        if artigo is None:
            continue                      # ementa, preambulo, titulos de capitulo

        # ------------------------------------------------------- paragrafo
        numerado = RE_PAR.match(linha)
        unico = None if numerado else RE_PAR_UNICO.match(linha)
        if numerado or unico:
            if numerado:
                letra = f"-{numerado.group(2)}" if numerado.group(2) else ""
                par_ref, par_txt = f"§ {numerado.group(1)}º{letra}", numerado.group(3)
            else:
                par_ref, par_txt = "parágrafo único", unico.group(1)  # type: ignore[union-attr]
            atual = None
            if not DESCARTAR.match(par_txt):
                atual = Dispositivo(
                    norma, f"art. {artigo}, {par_ref}", artigo, par_txt, contexto=caput or ""
                )
                saida.append(atual)
            continue

        # ---------------------------------------------------------- inciso
        if inciso := RE_INC.match(linha):
            if DESCARTAR.match(inciso.group(2)):
                atual = None              # inciso vetado: alineas dele tambem caem
                continue
            prefixo_par = f"{par_ref}, " if par_ref else ""
            referencia = f"art. {artigo}, {prefixo_par}{inciso.group(1)}"
            contexto = f"{caput} {par_txt}" if par_ref else (caput or "")
            atual = Dispositivo(norma, referencia, artigo, inciso.group(2), contexto=contexto)
            saida.append(atual)
            continue

        # ---------------------------------------------------------- alinea
        if (alinea := RE_ALI.match(linha)) and atual and not DESCARTAR.match(alinea.group(2)):
            atual.alineas.append(f"{alinea.group(1)}) {alinea.group(2)}")

    return saida


__all__ = ["Dispositivo", "segmentar", "numerar_artigo", "linhas_limpas", "decodificar", "TAMANHO_CONTEXTO"]
