"""Minimizacao de dados pessoais antes de qualquer envio externo (RN06).

Implementa o principio da necessidade (LGPD, art. 6º, III) e a protecao desde a
concepcao (art. 46, § 2º): os identificadores diretos sao substituidos por
marcadores antes da vetorizacao, antes do envio ao provedor de modelo de
linguagem e antes da gravacao da descricao no banco.

Limite conhecido e declarado no TCC: nomes proprios nao sao detectados por
expressao regular. O reconhecimento de entidades nomeadas fica registrado como
evolucao prevista, e a interface alerta o usuario (RN06).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A ordem importa: CNPJ antes de CPF, porque o padrao de CPF casaria com parte
# de um CNPJ nao formatado.
PADROES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CNPJ", re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")),
    ("CPF", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("TELEFONE", re.compile(r"(?:\(?\b\d{2}\)?\s?)?\b9?\d{4}-\d{4}\b")),
    ("CEP", re.compile(r"\b\d{5}-\d{3}\b")),
)


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


def minimizar(texto: str) -> ResultadoMinimizacao:
    """Substitui identificadores diretos por marcadores.

    >>> minimizar("contato joao@org.gov.br").texto
    'contato [EMAIL]'
    """
    if not texto:
        return ResultadoMinimizacao(texto="", ocorrencias={})

    resultado = texto
    ocorrencias: dict[str, int] = {}
    for rotulo, padrao in PADROES:
        achados = padrao.findall(resultado)
        if achados:
            ocorrencias[rotulo] = len(achados)
            resultado = padrao.sub(f"[{rotulo}]", resultado)
    return ResultadoMinimizacao(texto=resultado, ocorrencias=ocorrencias)


def contem_dado_pessoal(texto: str) -> bool:
    """Indica se ha identificador direto no texto, sem alterá-lo."""
    return any(padrao.search(texto or "") for _, padrao in PADROES)


__all__ = ["minimizar", "contem_dado_pessoal", "ResultadoMinimizacao", "PADROES"]
