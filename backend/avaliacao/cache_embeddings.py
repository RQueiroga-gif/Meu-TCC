"""Cache em disco dos vetores de um gerador de embeddings (so na avaliacao).

O BGE-M3 leva varios minutos em CPU para vetorizar o corpus (1.734 dispositivos).
Na repeticao do E9 para outro provedor (--somente-e9 --llm gemini depois de
--llm groq), o mesmo texto seria vetorizado de novo. Este invólucro guarda
cada vetor pela impressao digital (SHA-256) do texto e do identificador do
gerador: um texto ja visto nao volta ao modelo.

O cache fica fora da aplicacao de proposito: o sistema em producao nao muda, e
o identificador continua o do gerador original, de modo que o indice vetorial
construido com cache e indistinguivel do construido sem ele.

Nao use o cache quando o tempo de indexacao for medido (E1 e E6): ele mediria a
leitura do disco, nao o modelo. Por isso so e ligado no --somente-e9 ou com
--cache-embeddings.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import numpy as np

PASTA_CACHE = Path(__file__).resolve().parent / "cache"


class EmbedderComCache:
    """Mesmo contrato do Embedder, com memoria persistente dos vetores."""

    def __init__(self, base: Any, pasta: Path = PASTA_CACHE) -> None:
        self._base = base
        self.identificador = base.identificador
        self.dimensao = base.dimensao
        self.semantico = base.semantico
        self.limiar = base.limiar
        nome = re.sub(r"[^A-Za-z0-9._-]+", "_", self.identificador)
        self._arquivo = Path(pasta) / f"{nome}.npz"
        self._vetores: dict[str, np.ndarray] = {}
        self.acertos = self.calculados = 0
        if self._arquivo.exists():
            dados = np.load(self._arquivo, allow_pickle=False)
            self._vetores = dict(zip(dados["chaves"].tolist(), dados["vetores"], strict=True))

    def _chave(self, texto: str) -> str:
        return hashlib.sha256(f"{self.identificador}\x00{texto}".encode()).hexdigest()

    def codificar(self, textos: list[str]) -> np.ndarray:
        chaves = [self._chave(t) for t in textos]
        faltam = [i for i, c in enumerate(chaves) if c not in self._vetores]
        if faltam:
            novos = self._base.codificar([textos[i] for i in faltam])
            for i, vetor in zip(faltam, novos, strict=True):
                self._vetores[chaves[i]] = np.asarray(vetor, dtype=np.float32)
            self.calculados += len(faltam)
            self._salvar()
        self.acertos += len(textos) - len(faltam)
        if not textos:
            return np.zeros((0, self.dimensao), dtype=np.float32)
        return np.vstack([self._vetores[c] for c in chaves]).astype(np.float32)

    def _salvar(self) -> None:
        self._arquivo.parent.mkdir(parents=True, exist_ok=True)
        temporario = self._arquivo.with_suffix(".tmp.npz")
        np.savez_compressed(
            temporario,
            chaves=np.array(list(self._vetores), dtype="U64"),
            vetores=np.vstack(list(self._vetores.values())).astype(np.float32),
        )
        temporario.replace(self._arquivo)


__all__ = ["EmbedderComCache", "PASTA_CACHE"]
