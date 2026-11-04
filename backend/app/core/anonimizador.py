"""Minimizacao de dados pessoais antes de qualquer envio externo (RN06).

Implementa o principio da necessidade (LGPD, art. 6º, III) e a protecao desde a
concepcao (art. 46, § 2º): os identificadores sao substituidos por marcadores
antes da vetorizacao, antes do envio ao provedor de modelo de linguagem e antes
da gravacao da descricao no banco.

Duas camadas:

  * expressoes regulares para identificadores com formato definido (CPF, CNPJ
    numerico e alfanumerico, e-mail, telefone, CEP e RG). Numeros sem pontuacao
    so sao tratados como CPF ou CNPJ quando os digitos verificadores conferem ou
    quando o rotulo ("CPF", "CNPJ") os precede; telefones exigem DDD, +55, o
    formato de celular (9xxxx-xxxx) ou uma palavra de contato. Assim, codigos
    administrativos com formato parecido (empenho, PCA, processo) nao viram
    dado pessoal;
  * reconhecimento de entidades nomeadas (spaCy, pt_core_news_md) para nomes de
    pessoas, opcional (ANONIMIZADOR_NER). Apenas entidades PER com dois ou mais
    termos sao mascaradas, e nomes que integram denominacao de instituicao
    ("Escola Municipal Tancredo Neves") sao preservados.

Versao 0.3 (avaliacao de 26/10/2026): a versao 0.2.0 nao detectava nomes, RG,
CNPJ alfanumerico (IN RFB nº 2.229/2024), telefone sem hifen e CEP com ponto, e
mascarava como telefone codigos como "2027-0458". Os numeros do conjunto de
teste estao em avaliacao/dados/dados_pessoais.yaml.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from ..config import config

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ padroes
RE_CNPJ_FORMATADO = re.compile(r"\b[0-9A-Z]{2}\.[0-9A-Z]{3}\.[0-9A-Z]{3}/[0-9A-Z]{4}-\d{2}\b")
RE_CPF_FORMATADO = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
RE_14_DIGITOS = re.compile(r"\b\d{14}\b")
RE_11_DIGITOS = re.compile(r"\b\d{11}\b")
RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
RE_CEP = re.compile(r"\b\d{2}\.?\d{3}-\d{3}\b|(?<=CEP)\s*:?\s*\d{8}\b", re.I)
RE_RG = re.compile(r"(?<=\bRG)(\s*(?:n[º°o.]*)?\s*:?\s*)(\d{1,2}\.?\d{3}\.?\d{3}-?[\dXx])\b", re.I)

# Telefone: DDD entre parenteses ou separado, +55, celular 9xxxx-xxxx, ou palavra
# de contato logo antes. Sem nenhum desses sinais, "2027-0458" e so um codigo.
_NUMERO_FONE = r"(?:9\d{4}-?\d{4}|[2-5]\d{3}-?\d{4})"
RE_FONE_DDD = re.compile(
    r"(?:\+?55\s*)?(?:\(\s*\d{2}\s*\)\s*|\b\d{2}\s+)" + _NUMERO_FONE + r"\b"
)
RE_FONE_CELULAR = re.compile(r"(?:\+?55\s*)?\b(?:\d{2})?9\d{4}-\d{4}\b")
RE_FONE_CELULAR_SEM_HIFEN = re.compile(r"(?:\+?55\s*)?\b\d{2}9\d{8}\b")
RE_FONE_ROTULADO = re.compile(
    r"(\b(?:tel|fone|telefone|celular|whatsapp|contato|ligar\s+para)[\s.:]{1,3})"
    r"(" + _NUMERO_FONE + r")\b",
    re.I,
)

MARCADORES = {"CPF", "CNPJ", "EMAIL", "TELEFONE", "CEP", "RG", "NOME"}

INSTITUICOES = {
    "escola", "colégio", "colegio", "hospital", "rua", "avenida", "av", "praça",
    "praca", "rodovia", "ponte", "centro", "unidade", "creche", "ginásio",
    "ginasio", "estádio", "estadio", "instituto", "fundação", "fundacao",
    "presidente", "ministro", "governador", "prefeito", "senador", "deputado",
    "doutor", "dr", "professor", "professora", "prof", "santa", "são", "sao",
    "edifício", "edificio", "parque", "aeroporto", "terminal", "biblioteca",
    "museu", "teatro", "vila", "bairro", "jardim", "loteamento", "conjunto",
}


@dataclass(slots=True)
class ResultadoMinimizacao:
    texto: str
    ocorrencias: dict[str, int]

    @property
    def houve_remocao(self) -> bool:
        return bool(self.ocorrencias)

    @property
    def total(self) -> int:
        return sum(self.ocorrencias.values())


# ------------------------------------------------------- digitos verificadores
def _cpf_valido(digitos: str) -> bool:
    if len(digitos) != 11 or len(set(digitos)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(int(d) * (tamanho + 1 - i) for i, d in enumerate(digitos[:tamanho]))
        if (soma * 10 % 11) % 10 != int(digitos[tamanho]):
            return False
    return True


def _cnpj_valido(digitos: str) -> bool:
    if len(digitos) != 14 or len(set(digitos)) == 1:
        return False
    pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for tamanho in (12, 13):
        p = pesos if tamanho == 12 else [6, *pesos]
        resto = sum(int(d) * w for d, w in zip(digitos[:tamanho], p, strict=True)) % 11
        if (0 if resto < 2 else 11 - resto) != int(digitos[tamanho]):
            return False
    return True


def _rotulado(texto: str, inicio: int, rotulo: str) -> bool:
    return rotulo in texto[max(0, inicio - 15): inicio].upper()


# --------------------------------------------------------------- substituicao
class _Substituidor:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.ocorrencias: dict[str, int] = {}

    def _contar(self, rotulo: str, n: int = 1) -> None:
        if n:
            self.ocorrencias[rotulo] = self.ocorrencias.get(rotulo, 0) + n

    def padrao(self, rotulo: str, regex: re.Pattern[str]) -> None:
        novo, n = regex.subn(f"[{rotulo}]", self.texto)
        self.texto = novo
        self._contar(rotulo, n)

    def condicional(self, rotulo: str, regex: re.Pattern[str], aceitar) -> None:  # noqa: ANN001
        def trocar(achado: re.Match[str]) -> str:
            if aceitar(achado):
                self._contar(rotulo)
                return f"[{rotulo}]"
            return achado.group(0)

        self.texto = regex.sub(trocar, self.texto)

    def rotulado(self, rotulo: str, regex: re.Pattern[str]) -> None:
        """Mascara o grupo 2 e preserva o rotulo (grupo 1): 'RG [RG]', 'tel [TELEFONE]'."""
        def trocar(achado: re.Match[str]) -> str:
            self._contar(rotulo)
            return f"{achado.group(1)}[{rotulo}]"

        self.texto = regex.sub(trocar, self.texto)

    def rg(self) -> None:
        def trocar(achado: re.Match[str]) -> str:
            self._contar("RG")
            return f"{achado.group(1)}[RG]"

        self.texto = RE_RG.sub(trocar, self.texto)


def _identificadores(texto: str) -> _Substituidor:
    """Camada 1: identificadores com formato definido."""
    s = _Substituidor(texto)
    # A ordem importa: CNPJ antes de CPF e de telefone (formatos que se sobrepoem).
    s.padrao("CNPJ", RE_CNPJ_FORMATADO)
    s.condicional(
        "CNPJ", RE_14_DIGITOS,
        lambda m: _cnpj_valido(m.group(0)) or _rotulado(m.string, m.start(), "CNPJ"),
    )
    s.padrao("CPF", RE_CPF_FORMATADO)
    s.condicional(
        "CPF", RE_11_DIGITOS,
        lambda m: _cpf_valido(m.group(0)) or _rotulado(m.string, m.start(), "CPF"),
    )
    s.padrao("EMAIL", RE_EMAIL)
    s.padrao("TELEFONE", RE_FONE_DDD)
    s.padrao("TELEFONE", RE_FONE_CELULAR_SEM_HIFEN)
    s.padrao("TELEFONE", RE_FONE_CELULAR)
    s.rotulado("TELEFONE", RE_FONE_ROTULADO)
    s.padrao("CEP", RE_CEP)
    s.rg()
    return s


# ------------------------------------------------------------- nomes (NER)
@lru_cache(maxsize=1)
def _modelo_ner():
    """Carrega o modelo de entidades uma unica vez; None se indisponivel."""
    if not config.ANONIMIZADOR_NER:
        return None
    try:
        import spacy

        return spacy.load(
            config.ANONIMIZADOR_MODELO_NER,
            exclude=["parser", "lemmatizer", "attribute_ruler", "morphologizer", "senter"],
        )
    except Exception as erro:  # noqa: BLE001
        log.warning(
            "Reconhecimento de nomes indisponível (%s): apenas identificadores com "
            "formato definido serão minimizados. Instale o spaCy e o modelo %s.",
            erro,
            config.ANONIMIZADOR_MODELO_NER,
        )
        return None


def ner_disponivel() -> bool:
    return _modelo_ner() is not None


def _nomes(s: _Substituidor) -> None:
    """Camada 2: nomes de pessoas (entidades PER com dois ou mais termos)."""
    nlp = _modelo_ner()
    if nlp is None or not s.texto.strip():
        return
    doc = nlp(s.texto)
    trechos: list[tuple[int, int]] = []
    for ent in doc.ents:
        if ent.label_ != "PER" or len([t for t in ent if t.is_alpha]) < 2:
            continue
        if any(t.text.strip("[]").upper() in MARCADORES for t in ent):
            continue                      # o modelo tomou um marcador por nome
        anteriores = {t.lower_.strip(".") for t in doc[max(0, ent.start - 3): ent.start]}
        if anteriores & INSTITUICOES or ent[0].lower_ in INSTITUICOES:
            continue                      # nome que integra denominacao de instituicao
        trechos.append((ent.start_char, ent.end_char))
    for inicio, fim in reversed(trechos):
        s.texto = s.texto[:inicio] + "[NOME]" + s.texto[fim:]
    s._contar("NOME", len(trechos))


# ------------------------------------------------------------------- publico
def minimizar(texto: str, *, nomes: bool = True) -> ResultadoMinimizacao:
    """Substitui dados pessoais por marcadores.

    Args:
        texto: texto livre (demanda, campo da minuta, justificativa).
        nomes: aplica tambem o reconhecimento de nomes, se disponivel.

    >>> minimizar("contato joao@org.gov.br").texto
    'contato [EMAIL]'
    """
    if not texto:
        return ResultadoMinimizacao(texto="", ocorrencias={})
    s = _identificadores(texto)
    if nomes:
        _nomes(s)
    return ResultadoMinimizacao(texto=s.texto, ocorrencias=s.ocorrencias)


def contem_dado_pessoal(texto: str) -> bool:
    """Indica se ha dado pessoal no texto, sem altera-lo."""
    return minimizar(texto or "").houve_remocao


__all__ = ["minimizar", "contem_dado_pessoal", "ner_disponivel", "ResultadoMinimizacao"]
