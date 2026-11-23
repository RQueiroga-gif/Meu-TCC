"""Indice vetorial (RF03, RF12, RN03).

IndiceNumpy: busca exata por produto interno sobre vetores normalizados,
persistida em um arquivo .npz. Com o corpus deste trabalho (1.734 dispositivos)
a busca exata custa milissegundos e dispensa servidor, decisao de projeto
registrada no capitulo de metodologia. O contrato `Indice` permanece, de modo que
um banco vetorial dedicado pode ser acrescentado se o corpus crescer.

v0.4: o backend ChromaDB foi retirado. Nunca era exercitado pelos testes, nao
acompanhava o indice lexical (a fusao exige o BM25 local) e respondia a um
cenario de integracao (orquestrador n8n) que o projeto abandonou.

O filtro de vigencia (RN03) e aplicado na propria consulta: norma revogada nunca
chega a fundamentar uma minuta.

O indice numpy grava o identificador do gerador de embeddings que o construiu.
Se o gerador mudar (por exemplo, ao instalar o sentence-transformers depois do
seed), o indice antigo e descartado e o sistema pede reindexacao, em vez de
comparar vetores de espacos diferentes.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..config import config
from .embeddings import obter_embedder

log = logging.getLogger(__name__)


class IndiceIncompativel(RuntimeError):
    """O indice gravado foi construido por outro gerador de embeddings."""


@dataclass(slots=True)
class Ocorrencia:
    """Trecho recuperado, com a similaridade que o justificou."""

    referencia_vetorial: str
    referencia: str
    texto: str
    similaridade: float
    metadados: dict[str, Any]

    def para_dict(self, identificador: str | None = None) -> dict[str, Any]:
        saida = {
            "id": self.referencia_vetorial,
            "referencia": self.referencia,
            "texto": self.texto,
            "similaridade": round(self.similaridade, 4),
        }
        if identificador:
            saida["marcador"] = identificador   # T1, T2...
        return saida


@runtime_checkable
class Indice(Protocol):
    def indexar(self, itens: list[dict[str, Any]]) -> int: ...
    def buscar(self, consulta: str, k: int, apenas_vigentes: bool = True) -> list[Ocorrencia]: ...
    def remover(self, referencias: list[str]) -> int: ...
    def atualizar_vigencia(self, referencias: list[str], vigente: bool) -> int: ...
    def faltantes(self, referencias: list[str]) -> list[str]: ...
    def total(self) -> int: ...


class IndiceNumpy:
    """Busca exata sobre vetores normalizados, persistida em .npz."""

    # No modo hibrido, a mesma consulta e buscada duas vezes no indice denso (lista
    # do RRF e similaridade maxima do portao). Com o BGE-M3 em CPU, cada
    # vetorizacao custa ~0,2 s; a memoria das ultimas consultas evita a repeticao.
    MEMORIA_CONSULTAS = 32

    def __init__(self, caminho: str | Path | None = None) -> None:
        self._caminho = Path(caminho or config.INDICE_ARQUIVO)
        self._gerador: str = ""
        self._consultas: OrderedDict[tuple[str, str], np.ndarray] = OrderedDict()
        self._trava = threading.Lock()
        self._limpar()
        self._carregar()

    def _vetor_consulta(self, consulta: str) -> np.ndarray:
        """Vetor da consulta, reaproveitado se a mesma consulta acabou de ser feita."""
        embedder = obter_embedder()
        chave = (embedder.identificador, consulta)
        with self._trava:
            vetor = self._consultas.get(chave)
            if vetor is not None:
                self._consultas.move_to_end(chave)
                return vetor
        vetor = embedder.codificar([consulta])[0]
        with self._trava:
            self._consultas[chave] = vetor
            while len(self._consultas) > self.MEMORIA_CONSULTAS:
                self._consultas.popitem(last=False)
        return vetor

    def _limpar(self) -> None:
        self._ids: list[str] = []
        self._referencias: list[str] = []
        self._textos: list[str] = []
        self._vigentes: list[bool] = []
        self._vetores: np.ndarray = np.zeros((0, 0), dtype=np.float32)

    # ------------------------------------------------------------ persistencia
    def _carregar(self) -> None:
        if not self._caminho.exists():
            return
        try:
            try:
                dados = np.load(self._caminho, allow_pickle=False)
                _ = dados["ids"]
            except ValueError:
                # Formato da versao 0.1 (arrays de objeto): lido uma unica vez e
                # regravado no formato novo na proxima escrita.
                dados = np.load(self._caminho, allow_pickle=True)
            self._ids = [str(v) for v in dados["ids"]]
            self._referencias = [str(v) for v in dados["referencias"]]
            self._textos = [str(v) for v in dados["textos"]]
            self._vigentes = [bool(v) for v in dados["vigentes"]]
            self._vetores = dados["vetores"].astype(np.float32)
            self._gerador = str(dados["gerador"]) if "gerador" in dados.files else ""
            log.info("Indice carregado: %d trechos (%s)", len(self._ids), self._gerador or "?")
        except Exception as erro:  # noqa: BLE001
            log.error("Indice corrompido em %s (%s); iniciando vazio", self._caminho, erro)
            self._limpar()
            self._gerador = ""

    def _conferir_gerador(self) -> None:
        """Garante que o gerador ativo e o mesmo que construiu o indice."""
        atual = obter_embedder().identificador
        if not self._ids:
            self._gerador = atual
            return
        if self._gerador and self._gerador != atual:
            raise IndiceIncompativel(
                f"o indice foi construido com '{self._gerador}', mas o gerador ativo e "
                f"'{atual}'. Reindexe a base: python -m scripts.seed --reindexar"
            )
        if not self._gerador:
            # Indice legado sem identificador: aceito se a dimensao coincidir.
            if self._vetores.shape[1] != obter_embedder().dimensao:
                raise IndiceIncompativel(
                    "o indice tem dimensao diferente do gerador ativo. "
                    "Reindexe a base: python -m scripts.seed --reindexar"
                )
            self._gerador = atual

    def salvar(self) -> None:
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        # Arrays de texto (nao de objeto): o arquivo e lido sem pickle.
        np.savez_compressed(
            self._caminho,
            ids=np.array(self._ids, dtype=str),
            referencias=np.array(self._referencias, dtype=str),
            textos=np.array(self._textos, dtype=str),
            vigentes=np.array(self._vigentes, dtype=bool),
            vetores=self._vetores,
            gerador=np.array(self._gerador, dtype=str),
        )
        log.info("Indice gravado: %s (%d trechos)", self._caminho, len(self._ids))

    # ------------------------------------------------------------ operacoes
    def indexar(self, itens: list[dict[str, Any]]) -> int:
        """Insere ou atualiza trechos (upsert por referencia_vetorial)."""
        if not itens:
            return 0

        embedder = obter_embedder()
        if self._ids and self._gerador and self._gerador != embedder.identificador:
            # Troca de gerador durante uma reindexacao: recomeca do zero, pois
            # vetores de espacos diferentes nao podem coexistir.
            log.warning(
                "Gerador mudou (%s -> %s); o indice sera reconstruido",
                self._gerador,
                embedder.identificador,
            )
            self._limpar()
        self._gerador = embedder.identificador

        vetores = embedder.codificar([i["texto_indexado"] for i in itens])
        if self._vetores.size == 0:
            self._vetores = np.zeros((0, vetores.shape[1]), dtype=np.float32)
        if vetores.shape[1] != self._vetores.shape[1]:
            raise IndiceIncompativel(
                f"Dimensao incompativel: indice tem {self._vetores.shape[1]}, "
                f"gerador produziu {vetores.shape[1]}. Reindexe a base."
            )

        posicao = {ref: i for i, ref in enumerate(self._ids)}
        novos_vetores: list[np.ndarray] = []
        for item, vetor in zip(itens, vetores, strict=True):
            ref = item["referencia_vetorial"]
            if (idx := posicao.get(ref)) is not None and idx < len(self._vetores):
                self._referencias[idx] = item["referencia"]
                self._textos[idx] = item["texto_indexado"]
                self._vigentes[idx] = bool(item.get("vigente", True))
                self._vetores[idx] = vetor
            elif idx is not None:
                # repetido dentro do proprio lote: fica a ultima versao
                novos_vetores[idx - len(self._vetores)] = vetor
                self._referencias[idx] = item["referencia"]
                self._textos[idx] = item["texto_indexado"]
                self._vigentes[idx] = bool(item.get("vigente", True))
            else:
                posicao[ref] = len(self._ids)
                self._ids.append(ref)
                self._referencias.append(item["referencia"])
                self._textos.append(item["texto_indexado"])
                self._vigentes.append(bool(item.get("vigente", True)))
                novos_vetores.append(vetor)

        if novos_vetores:
            self._vetores = np.vstack([self._vetores, np.vstack(novos_vetores)])
        self.salvar()
        return len(itens)

    def buscar(self, consulta: str, k: int, apenas_vigentes: bool = True) -> list[Ocorrencia]:
        if not self._ids or not consulta.strip():
            return []
        self._conferir_gerador()

        vetor = self._vetor_consulta(consulta)
        similaridades = self._vetores @ vetor          # vetores normalizados: = cosseno

        mascara = np.array(self._vigentes) if apenas_vigentes else np.ones(len(self._ids), bool)
        candidatos = np.where(mascara)[0]
        if candidatos.size == 0:
            return []

        pontuacoes = similaridades[candidatos]
        melhores = candidatos[np.argsort(-pontuacoes)[: max(k, 1)]]

        return [
            Ocorrencia(
                referencia_vetorial=self._ids[i],
                referencia=self._referencias[i],
                texto=self._textos[i],
                similaridade=float(similaridades[i]),
                metadados={"vigente": self._vigentes[i]},
            )
            for i in melhores
        ]

    def remover(self, referencias: list[str]) -> int:
        alvo = set(referencias)
        manter = [i for i, ref in enumerate(self._ids) if ref not in alvo]
        removidos = len(self._ids) - len(manter)
        if removidos:
            self._ids = [self._ids[i] for i in manter]
            self._referencias = [self._referencias[i] for i in manter]
            self._textos = [self._textos[i] for i in manter]
            self._vigentes = [self._vigentes[i] for i in manter]
            self._vetores = self._vetores[manter] if manter else np.zeros((0, 0), np.float32)
            self.salvar()
        return removidos

    def atualizar_vigencia(self, referencias: list[str], vigente: bool) -> int:
        """Liga ou desliga trechos na busca sem recalcular vetores (RN03)."""
        alvo = set(referencias)
        alterados = 0
        for i, ref in enumerate(self._ids):
            if ref in alvo and self._vigentes[i] != vigente:
                self._vigentes[i] = vigente
                alterados += 1
        if alterados:
            self.salvar()
        return alterados

    def faltantes(self, referencias: list[str]) -> list[str]:
        """Referencias que existem no banco mas nao no indice."""
        presentes = set(self._ids)
        return [r for r in referencias if r not in presentes]

    def total(self) -> int:
        return len(self._ids)

    def itens(self) -> list[dict[str, Any]]:
        """Conteudo textual do indice (para reconstruir o indice lexical)."""
        return [
            {"referencia_vetorial": i, "referencia": r, "texto_indexado": t, "vigente": v}
            for i, r, t, v in zip(self._ids, self._referencias, self._textos, self._vigentes, strict=True)
        ]

    def limpar(self) -> None:
        """Esvazia o indice (usado na reindexacao completa)."""
        self._limpar()
        self._gerador = obter_embedder().identificador
        self.salvar()


_indice: Indice | None = None


def obter_indice() -> Indice:
    """Instancia unica do indice: vetorial (numpy) + lexical (BM25).

    O indice composto decide, pelo RECUPERACAO_MODO, qual dos dois responde a
    busca; ingestao, vigencia e reindexacao alimentam ambos.
    """
    global _indice
    if _indice is None:
        from .lexical import IndiceComposto, IndiceLexical

        denso = IndiceNumpy()
        lexical = IndiceLexical()
        if lexical.total() == 0 and denso.total():
            # Base indexada antes da versao 0.3: o texto ja esta no indice
            # vetorial, entao o lexical e reconstruido sem tocar no banco.
            lexical.indexar(denso.itens())
            log.info("Indice lexical reconstruido a partir do vetorial (%d trechos)", lexical.total())
        _indice = IndiceComposto(denso, lexical)
    return _indice


def redefinir_indice(instancia: Indice | None = None) -> None:
    """Substitui o indice global (usado em testes)."""
    global _indice
    _indice = instancia


__all__ = [
    "Indice",
    "IndiceNumpy",
    "IndiceIncompativel",
    "Ocorrencia",
    "obter_indice",
    "redefinir_indice",
]
