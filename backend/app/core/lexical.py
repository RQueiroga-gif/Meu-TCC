"""Indice lexical BM25 e indice composto (recuperacao densa, lexical ou hibrida).

Motivacao (avaliacao de 26/10/2026, capitulo de resultados): sem o BGE-M3, os
geradores de embeddings que rodam em qualquer maquina (hash de n-gramas e media
de vetores do spaCy) recuperaram o dispositivo esperado em menos de 20% dos
casos (R@6), enquanto o BM25 — linha de base classica da recuperacao de
informacao (ROBERTSON; ZARAGOZA, 2009) — chegou a 36% no mesmo gabarito. O
modo de recuperacao passou a ser configuravel:

  * denso    — apenas embeddings (desenho original; recomendado com BGE-M3);
  * lexical  — apenas BM25 (recomendado sem modelo semantico de qualidade);
  * hibrido  — fusao das duas listas por posicao reciproca (RRF; CORMACK;
               CLARKE; BUETTCHER, 2009);
  * automatico (padrao) — denso com BGE-M3; lexical com os demais geradores.

Em todos os modos, o portao de suficiencia (fluxo A1) usa o cosseno do gerador
de embeddings, cuja escala e calibrada por gerador (experimento E3).

O IndiceComposto implementa o mesmo contrato do indice vetorial, de modo que a
ingestao, a gestao de vigencia (RN03) e a reindexacao alimentam os dois indices
sem que o restante do sistema saiba que eles existem.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import config
from .indice import Indice, Ocorrencia

log = logging.getLogger(__name__)

PALAVRAS_VAZIAS = set(
    """a ao aos as à às até com como da das de dela dele deles demais depois do dos e
    ela elas ele eles em entre era essa essas esse esses esta estas este estes eu foi
    for foram há isso isto já la lhe lhes mais mas me mesmo meu minha muito na nas
    nem no nos nossa nosso num numa o os ou para pela pelas pelo pelos por qual quando
    que quem se sem ser seu seus sob sobre sua suas também te tem têm tendo ter teu
    toda todas todo todos tu um uma umas uns você vocês art inciso caput parágrafo
    lei nº n decreto deste desta desse dessa artigo""".split()
)


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


class Tokenizador:
    """Minusculas, sem acento, sem palavras vazias e com radical Snowball (portugues)."""

    def __init__(self) -> None:
        try:
            import snowballstemmer

            self._radical = snowballstemmer.stemmer("portuguese").stemWord
        except ImportError:  # pragma: no cover
            log.warning("snowballstemmer ausente: BM25 sem radicalizacao")
            self._radical = lambda palavra: palavra
        self._vazias = {_sem_acento(p) for p in PALAVRAS_VAZIAS}

    def __call__(self, texto: str) -> list[str]:
        palavras = re.findall(r"[a-z0-9]+", _sem_acento((texto or "").lower()))
        return [self._radical(p) for p in palavras if p not in self._vazias and len(p) > 1]


class IndiceLexical:
    """Okapi BM25 (k1 = 1,2; b = 0,75), persistido em JSON compactado.

    A pontuacao exibida e s / (s + C), transformacao monotonica da pontuacao
    BM25 bruta para [0, 1): preserva a ordenacao e da escala legivel na tela de
    fontes. Ela NAO alimenta o portao de suficiencia: o BM25 depende das
    estatisticas do corpus (idf e tamanho medio), que mudam sempre que o gestor
    inclui, atualiza ou desativa uma norma, e o limiar calibrado perderia a
    validade. O portao usa o cosseno do gerador de embeddings, cuja escala nao
    depende do corpus (ver IndiceComposto.similaridade_maxima). Na avaliacao
    (E3) a pontuacao BM25 tambem separou bem as demandas fora do dominio; o uso
    dela no portao, com recalibracao a cada alteracao da base, fica registrado
    como evolucao possivel.
    """

    K1, B = 1.2, 0.75
    C = 10.0

    def __init__(self, caminho: str | Path | None = None) -> None:
        self._caminho = Path(caminho or config.INDICE_LEXICAL_ARQUIVO)
        self._tok = Tokenizador()
        self._docs: dict[str, dict[str, Any]] = {}
        self._idf: dict[str, float] = {}
        self._media = 1.0
        self._carregar()

    # ------------------------------------------------------------ persistencia
    def _carregar(self) -> None:
        if not self._caminho.exists():
            return
        try:
            with gzip.open(self._caminho, "rt", encoding="utf-8") as f:
                self._docs = json.load(f)
            self._recalcular()
        except Exception as erro:  # noqa: BLE001
            log.error("Índice lexical corrompido (%s); iniciando vazio", erro)
            self._docs = {}

    def salvar(self) -> None:
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(self._caminho, "wt", encoding="utf-8") as f:
            json.dump(self._docs, f, ensure_ascii=False)

    def _recalcular(self) -> None:
        n = len(self._docs)
        df: Counter[str] = Counter()
        for doc in self._docs.values():
            df.update(doc["tf"].keys())
        self._idf = {t: math.log((n - f + 0.5) / (f + 0.5) + 1) for t, f in df.items()}
        self._media = (sum(d["tamanho"] for d in self._docs.values()) / n) if n else 1.0

    # ------------------------------------------------------------ contrato
    def indexar(self, itens: list[dict[str, Any]]) -> int:
        for item in itens:
            termos = self._tok(item["texto_indexado"])
            self._docs[item["referencia_vetorial"]] = {
                "referencia": item["referencia"],
                "texto": item["texto_indexado"],
                "vigente": bool(item.get("vigente", True)),
                "tf": dict(Counter(termos)),
                "tamanho": len(termos),
            }
        if itens:
            self._recalcular()
            self.salvar()
        return len(itens)

    def pontuar(self, consulta: str, apenas_vigentes: bool = True) -> list[tuple[str, float]]:
        termos = self._tok(consulta)
        if not termos or not self._docs:
            return []
        unicos = set(termos)
        pontos: list[tuple[str, float]] = []
        for ref, doc in self._docs.items():
            if apenas_vigentes and not doc["vigente"]:
                continue
            norma = self.K1 * (1 - self.B + self.B * doc["tamanho"] / self._media)
            s = 0.0
            for t in unicos:
                if f := doc["tf"].get(t):
                    s += self._idf[t] * f * (self.K1 + 1) / (f + norma)
            if s > 0:
                pontos.append((ref, s / (s + self.C)))
        pontos.sort(key=lambda x: -x[1])
        return pontos

    def buscar(self, consulta: str, k: int, apenas_vigentes: bool = True) -> list[Ocorrencia]:
        return [
            Ocorrencia(
                referencia_vetorial=ref,
                referencia=self._docs[ref]["referencia"],
                texto=self._docs[ref]["texto"],
                similaridade=float(s),
                metadados={"vigente": self._docs[ref]["vigente"], "origem": "lexical"},
            )
            for ref, s in self.pontuar(consulta, apenas_vigentes)[: max(k, 1)]
        ]

    def remover(self, referencias: list[str]) -> int:
        removidos = sum(1 for r in referencias if self._docs.pop(r, None) is not None)
        if removidos:
            self._recalcular()
            self.salvar()
        return removidos

    def atualizar_vigencia(self, referencias: list[str], vigente: bool) -> int:
        alterados = 0
        for r in referencias:
            if r in self._docs and self._docs[r]["vigente"] != vigente:
                self._docs[r]["vigente"] = vigente
                alterados += 1
        if alterados:
            self.salvar()
        return alterados

    def faltantes(self, referencias: list[str]) -> list[str]:
        return [r for r in referencias if r not in self._docs]

    def total(self) -> int:
        return len(self._docs)

    def limpar(self) -> None:
        self._docs = {}
        self._recalcular()
        self.salvar()


def modo_efetivo() -> str:
    """Modo de recuperacao em uso, resolvendo o 'automatico'.

    Com o BGE-M3, a fusao com o BM25 (hibrido) superou o denso puro no E1
    (R@6 0,396 x 0,242; Hit@6 0,688 x 0,438); sem ele, o BM25 supera os
    geradores offline.
    """
    from .embeddings import obter_embedder

    modo = config.RECUPERACAO_MODO
    if modo != "automatico":
        return modo
    return "hibrido" if obter_embedder().identificador.startswith("bge:") else "lexical"


class IndiceComposto:
    """Fachada sobre o indice vetorial e o lexical, com o contrato de Indice."""

    RRF_K = 60

    def __init__(self, denso: Indice, lexical: IndiceLexical) -> None:
        self.denso = denso
        self.lexical = lexical

    def indexar(self, itens: list[dict[str, Any]]) -> int:
        self.lexical.indexar(itens)
        return self.denso.indexar(itens)

    def buscar(self, consulta: str, k: int, apenas_vigentes: bool = True) -> list[Ocorrencia]:
        modo = modo_efetivo()
        if modo == "lexical":
            return self.lexical.buscar(consulta, k, apenas_vigentes)
        if modo == "denso":
            return self.denso.buscar(consulta, k, apenas_vigentes)
        # hibrido: RRF sobre listas mais longas.
        profundidade = max(k * 5, 30)
        densos = self.denso.buscar(consulta, profundidade, apenas_vigentes)
        lexicos = self.lexical.buscar(consulta, profundidade, apenas_vigentes)
        pontos: dict[str, float] = {}
        for lista in (densos, lexicos):
            for posicao, o in enumerate(lista, start=1):
                pontos[o.referencia_vetorial] = pontos.get(o.referencia_vetorial, 0.0) + 1 / (
                    self.RRF_K + posicao
                )
        por_ref = {o.referencia_vetorial: o for o in lexicos}
        por_ref.update({o.referencia_vetorial: o for o in densos})
        ordem = sorted(pontos, key=lambda r: -pontos[r])[: max(k, 1)]
        return [por_ref[r] for r in ordem]

    def similaridade_maxima(self, consulta: str) -> float:
        """Cosseno do trecho vigente mais proximo da consulta (portao de suficiencia)."""
        melhor = self.denso.buscar(consulta, 1, apenas_vigentes=True)
        return melhor[0].similaridade if melhor else 0.0

    def remover(self, referencias: list[str]) -> int:
        self.lexical.remover(referencias)
        return self.denso.remover(referencias)

    def atualizar_vigencia(self, referencias: list[str], vigente: bool) -> int:
        self.lexical.atualizar_vigencia(referencias, vigente)
        return self.denso.atualizar_vigencia(referencias, vigente)

    def faltantes(self, referencias: list[str]) -> list[str]:
        faltam = set(self.denso.faltantes(referencias)) | set(self.lexical.faltantes(referencias))
        return [r for r in referencias if r in faltam]

    def total(self) -> int:
        return min(self.denso.total(), self.lexical.total()) if self.lexical.total() else self.denso.total()

    def limpar(self) -> None:
        self.lexical.limpar()
        if hasattr(self.denso, "limpar"):
            self.denso.limpar()


__all__ = ["IndiceLexical", "IndiceComposto", "Tokenizador", "modo_efetivo"]
