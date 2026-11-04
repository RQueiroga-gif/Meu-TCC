"""Metricas de recuperacao e de classificacao (MANNING; RAGHAVAN; SCHUTZE, 2008).

Para cada consulta, `recuperados` e a lista ordenada de unidades devolvidas e
`relevantes` o conjunto de dispositivos do gabarito. Uma unidade pode cobrir
varios dispositivos (blocos fixos); um dispositivo e uma unidade so.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Sequence


def precisao_k(cobertura: Sequence[set[str]], relevantes: set[str], k: int) -> float:
    """Fracao das k primeiras unidades que cobrem ao menos um dispositivo relevante."""
    topo = cobertura[:k]
    if not topo:
        return 0.0
    return sum(1 for c in topo if c & relevantes) / k


def revocacao_k(cobertura: Sequence[set[str]], relevantes: set[str], k: int) -> float:
    """Fracao dos dispositivos relevantes cobertos pelas k primeiras unidades."""
    if not relevantes:
        return 0.0
    cobertos = set().union(*cobertura[:k]) if cobertura[:k] else set()
    return len(cobertos & relevantes) / len(relevantes)


def acerto_k(cobertura: Sequence[set[str]], relevantes: set[str], k: int) -> float:
    """1 se ao menos um relevante esta entre as k primeiras unidades."""
    return float(any(c & relevantes for c in cobertura[:k]))


def rr(cobertura: Sequence[set[str]], relevantes: set[str]) -> float:
    """Inverso da posicao do primeiro acerto (0 se nao houver)."""
    for i, c in enumerate(cobertura, start=1):
        if c & relevantes:
            return 1.0 / i
    return 0.0


def ndcg_k(cobertura: Sequence[set[str]], relevantes: set[str], k: int) -> float:
    """nDCG binario (JARVELIN; KEKALAINEN, 2002)."""
    ganho = sum(
        1.0 / math.log2(i + 1) for i, c in enumerate(cobertura[:k], start=1) if c & relevantes
    )
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(k, len(relevantes)) + 1))
    return ganho / ideal if ideal else 0.0


def media(valores: Iterable[float]) -> float:
    valores = list(valores)
    return sum(valores) / len(valores) if valores else float("nan")


def ic_bootstrap(
    valores: Sequence[float],
    estatistica: Callable[[Sequence[float]], float] = media,
    reamostragens: int = 2000,
    nivel: float = 0.95,
    semente: int = 42,
) -> tuple[float, float]:
    """Intervalo de confianca por bootstrap percentil (EFRON; TIBSHIRANI, 1993)."""
    if not valores:
        return (float("nan"), float("nan"))
    gerador = random.Random(semente)
    n = len(valores)
    estimativas = sorted(
        estatistica([valores[gerador.randrange(n)] for _ in range(n)]) for _ in range(reamostragens)
    )
    cauda = (1 - nivel) / 2
    return (
        estimativas[int(cauda * reamostragens)],
        estimativas[min(int((1 - cauda) * reamostragens), reamostragens - 1)],
    )


def auc(positivos: Sequence[float], negativos: Sequence[float]) -> float:
    """Area sob a curva ROC pela estatistica de Mann-Whitney (FAWCETT, 2006)."""
    if not positivos or not negativos:
        return float("nan")
    soma = 0.0
    for p in positivos:
        for q in negativos:
            soma += 1.0 if p > q else 0.5 if p == q else 0.0
    return soma / (len(positivos) * len(negativos))


def limiar_youden(positivos: Sequence[float], negativos: Sequence[float]) -> float:
    """Limiar que maximiza TPR - FPR; empates resolvidos pelo ponto medio."""
    candidatos = sorted(set(positivos) | set(negativos))
    melhor, limiar = -1.0, candidatos[0] if candidatos else 0.0
    for i, c in enumerate(candidatos):
        tpr = sum(p >= c for p in positivos) / len(positivos)
        fpr = sum(q >= c for q in negativos) / len(negativos)
        if tpr - fpr > melhor:
            melhor = tpr - fpr
            anterior = candidatos[i - 1] if i else c
            limiar = (c + anterior) / 2 if i else c
    return limiar


def _postos(valores: Sequence[float]) -> list[float]:
    ordem = sorted(range(len(valores)), key=lambda i: valores[i])
    postos = [0.0] * len(valores)
    i = 0
    while i < len(ordem):
        j = i
        while j + 1 < len(ordem) and valores[ordem[j + 1]] == valores[ordem[i]]:
            j += 1
        for t in range(i, j + 1):
            postos[ordem[t]] = (i + j) / 2 + 1
        i = j + 1
    return postos


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """Correlacao de postos de Spearman (empates pela media dos postos)."""
    if len(x) < 3:
        return float("nan")
    rx, ry = _postos(x), _postos(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else float("nan")


def percentil(valores: Sequence[float], p: float) -> float:
    if not valores:
        return float("nan")
    ordenados = sorted(valores)
    pos = (len(ordenados) - 1) * p / 100
    baixo, alto = math.floor(pos), math.ceil(pos)
    if baixo == alto:
        return ordenados[int(pos)]
    return ordenados[baixo] + (ordenados[alto] - ordenados[baixo]) * (pos - baixo)


__all__ = [
    "precisao_k",
    "revocacao_k",
    "acerto_k",
    "rr",
    "ndcg_k",
    "media",
    "ic_bootstrap",
    "auc",
    "limiar_youden",
    "percentil",
    "spearman",
]
