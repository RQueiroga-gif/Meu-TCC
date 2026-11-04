"""Geracao de embeddings, com implementacao intercambiavel.

Tres implementacoes atras do mesmo contrato, todas executadas localmente
(nenhum texto sai do ambiente para ser vetorizado):

  * EmbedderBGE — BGE-M3 via sentence-transformers: o gerador de referencia do
    trabalho, multilingue e de alta qualidade semantica (download de ~2 GB);
  * EmbedderSpacy — media dos vetores de palavras do modelo pt_core_news_md do
    spaCy (~40 MB): semantica limitada, mas sem GPU e sem acesso ao Hugging
    Face; usado na avaliacao offline e como alternativa leve;
  * EmbedderHash — hash de n-gramas de caracteres, deterministico e sem
    qualidade semantica, para testes automatizados.

A escolha e feita por configuracao (EMBEDDING_MODELO: "BAAI/bge-m3",
"spacy:pt_core_news_md" ou "hash"), nunca por importacao condicional espalhada
pelo codigo. Cada gerador declara o proprio limiar de suficiencia, porque as
escalas de similaridade diferem entre eles.
Cada gerador tem um `identificador`, gravado junto com o indice vetorial: um
indice construido por um gerador nunca e consultado com vetores de outro.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Protocol, runtime_checkable

import numpy as np

from ..config import config

log = logging.getLogger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """Contrato de um gerador de embeddings."""

    dimensao: int
    identificador: str   # ex.: "bge:BAAI/bge-m3" ou "hash:256:3"
    semantico: bool      # False: similaridades em escala reduzida (limiar proprio)
    limiar: float        # limiar de suficiencia calibrado para este gerador

    def codificar(self, textos: list[str]) -> np.ndarray:
        """Devolve uma matriz (n, dimensao) de vetores normalizados."""
        ...


def _normalizar_linhas(matriz: np.ndarray) -> np.ndarray:
    """Normaliza cada linha para norma 1, de modo que o produto interno seja o cosseno."""
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return matriz / normas


class EmbedderBGE:
    """BGE-M3 via sentence-transformers, execucao local (CHEN et al., 2024b)."""

    dimensao = 1024
    semantico = True

    def __init__(self, nome_modelo: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer  # import tardio

        self._nome = nome_modelo or config.EMBEDDING_MODELO
        log.info("Carregando modelo de embeddings: %s", self._nome)
        self._modelo = SentenceTransformer(self._nome)
        self.dimensao = int(self._modelo.get_sentence_embedding_dimension())
        self.identificador = f"bge:{self._nome}"
        self.limiar = config.RECUPERACAO_LIMIAR

    def codificar(self, textos: list[str]) -> np.ndarray:
        vetores = self._modelo.encode(
            textos, normalize_embeddings=True, batch_size=16, show_progress_bar=False
        )
        return np.asarray(vetores, dtype=np.float32)


class EmbedderSpacy:
    """Media dos vetores de palavras do spaCy (pt_core_news_md), execucao local.

    Representacao classica de sentenca por media de vetores estaticos: captura
    sinonimia e proximidade tematica ("notebooks" ~ "computadores portateis"),
    mas ignora a ordem e o contexto das palavras. Palavras vazias e tokens sem
    vetor sao descartados para que nao dominem a media.
    """

    semantico = True

    def __init__(self, nome_modelo: str = "pt_core_news_md") -> None:
        import spacy   # import tardio: dependencia opcional

        self._nome = nome_modelo
        # Apenas o tokenizador e a tabela de vetores sao necessarios.
        self._nlp = spacy.load(
            nome_modelo,
            exclude=["tok2vec", "morphologizer", "parser", "lemmatizer",
                     "attribute_ruler", "ner", "senter"],
        )
        self.dimensao = int(self._nlp.vocab.vectors.shape[1])
        self.identificador = f"spacy:{nome_modelo}"
        self.limiar = config.RECUPERACAO_LIMIAR_SPACY

    def _vetor(self, doc) -> np.ndarray:  # noqa: ANN001
        uteis = [t.vector for t in doc if t.has_vector and not t.is_stop and t.is_alpha]
        if not uteis:
            uteis = [t.vector for t in doc if t.has_vector]
        if not uteis:
            return np.zeros(self.dimensao, dtype=np.float32)
        return np.mean(uteis, axis=0).astype(np.float32)

    def codificar(self, textos: list[str]) -> np.ndarray:
        if not textos:
            return np.zeros((0, self.dimensao), dtype=np.float32)
        matriz = np.vstack([self._vetor(d) for d in self._nlp.pipe(textos, batch_size=64)])
        return _normalizar_linhas(matriz)


class EmbedderHash:
    """Gerador deterministico para testes e execucao sem o modelo baixado.

    Projeta n-gramas de caracteres em um espaco de dimensao fixa por hashing. Nao
    captura semantica: serve para exercitar indexacao, busca e fluxo, nunca para
    produzir as metricas do capitulo de resultados.
    """

    semantico = False

    def __init__(self, dimensao: int = 256, n: int = 3) -> None:
        self.dimensao = dimensao
        self._n = n
        self.identificador = f"hash:{dimensao}:{n}"
        self.limiar = config.RECUPERACAO_LIMIAR_HASH

    def _vetor(self, texto: str) -> np.ndarray:
        limpo = re.sub(r"\s+", " ", (texto or "").lower()).strip()
        vetor = np.zeros(self.dimensao, dtype=np.float32)
        if not limpo:
            return vetor
        for i in range(max(len(limpo) - self._n + 1, 1)):
            grama = limpo[i : i + self._n]
            digest = hashlib.blake2b(grama.encode("utf-8"), digest_size=8).digest()
            indice = int.from_bytes(digest[:4], "big") % self.dimensao
            sinal = 1.0 if digest[4] % 2 == 0 else -1.0
            vetor[indice] += sinal
        return vetor

    def codificar(self, textos: list[str]) -> np.ndarray:
        matriz = np.vstack([self._vetor(t) for t in textos]) if textos else np.zeros(
            (0, self.dimensao), dtype=np.float32
        )
        return _normalizar_linhas(matriz)


_embedder: Embedder | None = None


def obter_embedder(forcar: str | None = None) -> Embedder:
    """Devolve o gerador configurado (instancia unica).

    Args:
        forcar: "bge", "spacy" ou "hash" para sobrepor a configuracao
            (usado em testes e na avaliacao).
    """
    global _embedder

    if _embedder is not None and forcar is None:
        return _embedder

    modelo = (config.EMBEDDING_MODELO or "").strip()
    if forcar:
        escolha = forcar
    elif modelo.lower() in {"", "hash"}:
        escolha = "hash"
    elif modelo.lower().startswith("spacy"):
        escolha = "spacy"
    else:
        escolha = "bge"

    instancia: Embedder
    try:
        if escolha == "bge":
            instancia = EmbedderBGE()
        elif escolha == "spacy":
            _, _, nome = modelo.partition(":")
            instancia = EmbedderSpacy(nome or "pt_core_news_md")
        else:
            instancia = EmbedderHash()
    except Exception as erro:  # noqa: BLE001 — ausencia da biblioteca ou do modelo
        log.warning(
            "Gerador de embeddings '%s' indisponivel (%s); usando o deterministico. "
            "As metricas do capitulo exigem um modelo semantico "
            "(pip install sentence-transformers, ou spaCy com pt_core_news_md).",
            escolha,
            erro,
        )
        instancia = EmbedderHash()

    if forcar is None:
        _embedder = instancia
    return instancia


def redefinir_embedder(instancia: Embedder | None = None) -> None:
    """Substitui o gerador global (usado em testes)."""
    global _embedder
    _embedder = instancia


__all__ = [
    "Embedder",
    "EmbedderBGE",
    "EmbedderSpacy",
    "EmbedderHash",
    "obter_embedder",
    "redefinir_embedder",
]
