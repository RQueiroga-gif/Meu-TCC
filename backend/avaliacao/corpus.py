"""Corpus da avaliacao: dispositivos (unidade do sistema) e blocos fixos (controle).

Os dispositivos saem do mesmo segmentador usado pela aplicacao, aplicado ao
texto oficial de ingestao/oficial. Os blocos de tamanho fixo reproduzem a
alternativa do Experimento 3 do planejamento: o mesmo texto limpo (sem tachados
nem anotacoes), cortado em janelas de N palavras com sobreposicao, sem respeitar
a estrutura do ato normativo. Cada bloco guarda quais dispositivos cobre, para
que a relevancia possa ser julgada pelo mesmo gabarito.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.segmentador import Dispositivo, decodificar, segmentar

RAIZ_AVALIACAO = Path(__file__).resolve().parent
OFICIAL = RAIZ_AVALIACAO.parent.parent / "ingestao" / "oficial"

NORMAS = (
    ("L14133", "Lei nº 14.133/2021"),
    ("D10947", "Decreto nº 10.947/2022"),
    ("L13709", "Lei nº 13.709/2018"),
)


@dataclass(slots=True)
class Unidade:
    """Unidade de recuperacao (dispositivo ou bloco)."""

    ident: str                    # referencia_vetorial ("L14133|art. 18, I") ou "L14133#b0042"
    sigla: str
    citacao: str
    texto_indexado: str
    cobre: tuple[str, ...]        # dispositivos (referencia_vetorial) contidos na unidade
    artigos: tuple[str, ...] = field(default_factory=tuple)


def _linha_original(d: Dispositivo) -> str:
    """Reconstitui a linha como aparece no ato ("Art. 18.", "I -", "§ 1º")."""
    partes = d.referencia.split(", ")
    final = partes[-1]
    if final == "caput":
        return f"Art. {d.artigo}. {d.texto_completo}"
    if final.startswith("§") or final.startswith("parágrafo"):
        rotulo = "Parágrafo único." if final.startswith("parágrafo") else final
        return f"{rotulo} {d.texto_completo}"
    return f"{final} - {d.texto_completo}"


@lru_cache(maxsize=1)
def dispositivos() -> dict[str, list[Dispositivo]]:
    saida: dict[str, list[Dispositivo]] = {}
    for sigla, citacao in NORMAS:
        arquivo = OFICIAL / f"{sigla}.htm"
        if arquivo.exists():
            saida[sigla] = segmentar(decodificar(arquivo.read_bytes()), citacao)
    return saida


def hashes() -> dict[str, str]:
    return {
        sigla: hashlib.sha256((OFICIAL / f"{sigla}.htm").read_bytes()).hexdigest()
        for sigla, _ in NORMAS
        if (OFICIAL / f"{sigla}.htm").exists()
    }


def unidades_por_dispositivo() -> list[Unidade]:
    """A segmentacao do sistema: uma unidade por dispositivo."""
    unidades: list[Unidade] = []
    for sigla, lista in dispositivos().items():
        vistos: set[str] = set()
        for d in lista:
            ident = f"{sigla}|{d.referencia}"
            if ident in vistos:
                continue
            vistos.add(ident)
            unidades.append(
                Unidade(ident, sigla, d.citacao, d.texto_indexado(), (ident,), (d.artigo,))
            )
    return unidades


def unidades_por_bloco(palavras: int, sobreposicao: float = 0.2) -> list[Unidade]:
    """Blocos de `palavras` palavras com sobreposicao, sobre o texto corrido do ato.

    Um bloco cobre um dispositivo quando contem ao menos metade das palavras dele.
    """
    passo = max(1, int(palavras * (1 - sobreposicao)))
    unidades: list[Unidade] = []
    for sigla, lista in dispositivos().items():
        tokens: list[str] = []
        dono: list[str] = []          # dispositivo de cada palavra
        artigo_de: dict[str, str] = {}
        tamanho: dict[str, int] = {}
        for d in lista:
            ident = f"{sigla}|{d.referencia}"
            artigo_de[ident] = d.artigo
            linha = _linha_original(d).split()
            tokens.extend(linha)
            dono.extend([ident] * len(linha))
            tamanho[ident] = tamanho.get(ident, 0) + len(linha)
        inicio, n = 0, 0
        while inicio < len(tokens):
            fim = min(inicio + palavras, len(tokens))
            contagem: dict[str, int] = {}
            for ident in dono[inicio:fim]:
                contagem[ident] = contagem.get(ident, 0) + 1
            cobre = tuple(i for i, c in contagem.items() if c >= max(1, tamanho[i] / 2))
            unidades.append(
                Unidade(
                    ident=f"{sigla}#b{n:04d}",
                    sigla=sigla,
                    citacao=f"{sigla}, bloco {n}",
                    texto_indexado=" ".join(tokens[inicio:fim]),
                    cobre=cobre,
                    artigos=tuple(sorted({artigo_de[i] for i in contagem})),
                )
            )
            n += 1
            if fim == len(tokens):
                break
            inicio += passo
    return unidades


def gabarito() -> dict:
    return yaml.safe_load((RAIZ_AVALIACAO / "dados" / "gabarito.yaml").read_text(encoding="utf-8"))


def artigo_de(ident: str) -> str:
    """'L14133|art. 18, § 1º, I' -> 'L14133|18'."""
    sigla, _, ref = ident.partition("|")
    numero = ref.removeprefix("art. ").split(",")[0].strip()
    return f"{sigla}|{numero}"


__all__ = [
    "Unidade",
    "dispositivos",
    "hashes",
    "unidades_por_dispositivo",
    "unidades_por_bloco",
    "gabarito",
    "artigo_de",
]
