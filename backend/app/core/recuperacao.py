"""Recuperacao contextual com portao de suficiencia (RF03, RN03, fluxo A1).

O portao de suficiencia e o que impede o sistema de gerar texto sem fundamento:
se nenhum trecho recuperado superar o limiar de similaridade, a geracao nao
acontece e o usuario recebe orientacao para reformular a demanda. E a primeira
das tres camadas de controle contra alucinacao descritas no TCC.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..config import config
from .anonimizador import minimizar
from .embeddings import obter_embedder
from .indice import Ocorrencia, obter_indice
from .lexical import modo_efetivo

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ResultadoRecuperacao:
    """Saida da etapa de recuperacao, pronta para montar o contexto do prompt."""

    suficiente: bool
    trechos: list[Ocorrencia]
    descartados: int
    consulta_minimizada: str
    limiar: float

    @property
    def marcadores(self) -> dict[str, Ocorrencia]:
        """Mapa T1..Tn -> trecho, na ordem de similaridade."""
        return {f"T{i}": t for i, t in enumerate(self.trechos, start=1)}

    def contexto_para_dict(self) -> list[dict[str, Any]]:
        return [
            t.para_dict(identificador=f"T{i}")
            for i, t in enumerate(self.trechos, start=1)
        ]

    def bloco_contexto(self) -> str:
        """Bloco CONTEXTO do prompt, com os trechos numerados."""
        return "\n\n".join(
            f"[T{i}] {t.referencia} — {t.texto}"
            for i, t in enumerate(self.trechos, start=1)
        )


def limiar_padrao() -> float:
    """Limiar calibrado para o gerador de embeddings ativo.

    Cada gerador produz similaridades em escala propria: o deterministico fica
    bem abaixo do BGE-M3, e a media de vetores do spaCy bem acima. Aplicar a um
    gerador o limiar de outro barraria toda geracao ou nenhuma.
    """
    embedder = obter_embedder()
    limiar = getattr(embedder, "limiar", None)
    if limiar is not None:
        return float(limiar)
    return config.RECUPERACAO_LIMIAR if embedder.semantico else config.RECUPERACAO_LIMIAR_HASH


def recuperar(
    consulta: str,
    *,
    k: int | None = None,
    limiar: float | None = None,
    minimizar_antes: bool = True,
) -> ResultadoRecuperacao:
    """Recupera os dispositivos normativos vigentes pertinentes a demanda.

    Args:
        consulta: descricao da necessidade administrativa.
        k: quantidade de trechos a considerar (padrao: RECUPERACAO_TOP_K).
        limiar: similaridade minima (padrao: RECUPERACAO_LIMIAR, calibrado na F2).
        minimizar_antes: aplica a minimizacao de dados pessoais antes de vetorizar
            (RN06). Mantido como parametro apenas para os experimentos.
    """
    k = k if k is not None else config.RECUPERACAO_TOP_K
    limiar = limiar if limiar is not None else limiar_padrao()

    texto = minimizar(consulta).texto if minimizar_antes else consulta
    if not texto.strip():
        return ResultadoRecuperacao(False, [], 0, texto, limiar)

    indice = obter_indice()
    candidatos = indice.buscar(texto, k=k, apenas_vigentes=True)
    modo = modo_efetivo() if hasattr(indice, "similaridade_maxima") else "denso"
    if modo == "denso":
        relevantes = [t for t in candidatos if t.similaridade >= limiar]
    else:
        # Lexical e hibrido: a ordenacao vem do BM25 (ou da fusao), mas o portao
        # usa a similaridade de cosseno do melhor trecho da base, cuja escala e
        # calibrada por gerador e nao muda quando a base normativa e alterada.
        melhor = indice.similaridade_maxima(texto)
        relevantes = candidatos if melhor >= limiar else []

    log.info(
        "Recuperacao (%s): %d candidatos, %d aceitos pelo portao (limiar %.2f)",
        modo,
        len(candidatos),
        len(relevantes),
        limiar,
    )
    return ResultadoRecuperacao(
        suficiente=bool(relevantes),
        trechos=relevantes,
        descartados=len(candidatos) - len(relevantes),
        consulta_minimizada=texto,
        limiar=limiar,
    )


__all__ = ["recuperar", "limiar_padrao", "ResultadoRecuperacao"]
