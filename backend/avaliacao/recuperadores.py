"""Recuperadores comparados na avaliacao.

  * RecuperadorDenso — o caminho real do sistema: o gerador de embeddings da
    aplicacao (BGE-M3, spaCy ou hash) e o IndiceNumpy (busca exata por cosseno),
    sobre um indice temporario que nao toca a base de desenvolvimento;
  * RecuperadorBM25 — o indice lexical do sistema (BM25 normalizado, com remocao
    de palavras vazias e radicalizacao Snowball; ROBERTSON; ZARAGOZA, 2009);
  * RecuperadorHibrido — a fusao RRF do modo hibrido do sistema.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from app.core.embeddings import Embedder, redefinir_embedder
from app.core.indice import IndiceNumpy

from .corpus import Unidade

class Recuperador:
    nome: str = "abstrato"
    tempo_indexacao_s: float = 0.0

    def buscar(self, consulta: str, k: int) -> list[tuple[str, float]]:  # pragma: no cover
        raise NotImplementedError


class RecuperadorDenso(Recuperador):
    """Embeddings da aplicacao + IndiceNumpy (o mesmo codigo da busca em producao)."""

    def __init__(self, nome: str, embedder: Embedder, unidades: list[Unidade]) -> None:
        self.nome = nome
        self.embedder = embedder
        self._dir = tempfile.TemporaryDirectory(prefix="dfd-aval-")
        redefinir_embedder(embedder)
        self.indice = IndiceNumpy(Path(self._dir.name) / "indice.npz")
        inicio = time.perf_counter()
        self.indice.indexar(
            [
                {
                    "referencia_vetorial": u.ident,
                    "referencia": u.citacao,
                    "texto_indexado": u.texto_indexado,
                    "vigente": True,
                }
                for u in unidades
            ]
        )
        self.tempo_indexacao_s = time.perf_counter() - inicio

    def buscar(self, consulta: str, k: int) -> list[tuple[str, float]]:
        redefinir_embedder(self.embedder)
        return [(o.referencia_vetorial, o.similaridade) for o in self.indice.buscar(consulta, k=k)]


class RecuperadorBM25(Recuperador):
    """BM25 normalizado do proprio sistema (app.core.lexical.IndiceLexical)."""

    def __init__(self, unidades: list[Unidade]) -> None:
        from app.core.lexical import IndiceLexical

        self.nome = "BM25"
        self._dir = tempfile.TemporaryDirectory(prefix="dfd-aval-lex-")
        inicio = time.perf_counter()
        self.indice = IndiceLexical(Path(self._dir.name) / "lexical.json.gz")
        self.indice.indexar(
            [
                {"referencia_vetorial": u.ident, "referencia": u.citacao,
                 "texto_indexado": u.texto_indexado, "vigente": True}
                for u in unidades
            ]
        )
        self.tempo_indexacao_s = time.perf_counter() - inicio

    def buscar(self, consulta: str, k: int) -> list[tuple[str, float]]:
        return [(o.referencia_vetorial, o.similaridade) for o in self.indice.buscar(consulta, k)]


class RecuperadorHibrido(Recuperador):
    """Fusao RRF do sistema (app.core.lexical.IndiceComposto, modo hibrido)."""

    def __init__(self, denso: RecuperadorDenso, lexical: RecuperadorBM25) -> None:
        from app.core.lexical import IndiceComposto

        self.nome = f"Híbrido ({denso.nome} + BM25)"
        self._denso = denso
        self._composto = IndiceComposto(denso.indice, lexical.indice)
        self.tempo_indexacao_s = denso.tempo_indexacao_s + lexical.tempo_indexacao_s

    def buscar(self, consulta: str, k: int) -> list[tuple[str, float]]:
        from app.config import config

        redefinir_embedder(self._denso.embedder)
        anterior = config.RECUPERACAO_MODO
        config.RECUPERACAO_MODO = "hibrido"
        try:
            return [(o.referencia_vetorial, o.similaridade) for o in self._composto.buscar(consulta, k)]
        finally:
            config.RECUPERACAO_MODO = anterior


__all__ = ["Recuperador", "RecuperadorDenso", "RecuperadorBM25", "RecuperadorHibrido"]
