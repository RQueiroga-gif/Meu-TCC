"""Ingestao da base normativa: segmentar, persistir e indexar (F1 + F2).

Une as duas fases em uma operacao transacional: os trechos so entram no indice
vetorial se tambem tiverem sido gravados no banco, de modo que a rastreabilidade
(RN02) nunca aponte para um trecho inexistente.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.indice import obter_indice
from ..core.segmentador import Dispositivo, decodificar, segmentar
from ..models import Norma, TrechoNormativo, agora

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ResultadoIngestao:
    sigla: str
    dispositivos: int
    indexados: int
    hash_fonte: str
    alterada: bool

    def __str__(self) -> str:  # pragma: no cover
        estado = "atualizada" if self.alterada else "sem alteracao"
        extra = f", {self.indexados} reindexado(s)" if not self.alterada and self.indexados else ""
        return f"{self.sigla}: {self.dispositivos} dispositivos ({estado}{extra})"


def _item_indice(trecho: TrechoNormativo, citacao: str) -> dict:
    return {
        "referencia_vetorial": trecho.referencia_vetorial,
        "referencia": citacao,
        "texto_indexado": trecho.texto_indexado,
        "vigente": bool(trecho.vigente),
    }


def _sem_repeticao(dispositivos: list[Dispositivo], sigla: str) -> list[Dispositivo]:
    """Funde dispositivos com a mesma referencia.

    O HTML oficial as vezes repete a numeracao (por exemplo, dispositivos de
    outras leis citados em artigos que as alteram). A referencia e unica no
    banco, entao o texto repetido e anexado ao primeiro, sem perda.
    """
    unicos: dict[str, Dispositivo] = {}
    for d in dispositivos:
        if (existente := unicos.get(d.referencia)) is None:
            unicos[d.referencia] = d
            continue
        log.warning("%s: dispositivo repetido (%s); textos fundidos", sigla, d.referencia)
        existente.texto = f"{existente.texto} {d.texto}".strip()
        existente.alineas.extend(d.alineas)
    return list(unicos.values())


def ingerir(
    sessao: Session,
    *,
    bruto: bytes,
    sigla: str,
    tipo: str,
    numero: str,
    ano: int,
    ementa: str,
    url_fonte: str | None = None,
    forcar: bool = False,
) -> ResultadoIngestao:
    """Processa um ato normativo e o disponibiliza para recuperacao.

    Args:
        bruto: bytes do HTML oficial, como recebidos da fonte.
        forcar: reindexa mesmo que o hash da fonte nao tenha mudado.
    """
    hash_fonte = hashlib.sha256(bruto).hexdigest()
    html = decodificar(bruto)
    indice = obter_indice()

    norma = sessao.execute(select(Norma).where(Norma.sigla == sigla)).scalar_one_or_none()
    if norma is not None and norma.hash_fonte == hash_fonte and not forcar:
        trechos = list(
            sessao.execute(
                select(TrechoNormativo).where(TrechoNormativo.id_norma == norma.id_norma)
            ).scalars()
        )
        # Fonte igual, mas o indice pode ter sido apagado ou reconstruido:
        # repoe apenas o que falta, sem refazer a segmentacao.
        faltantes = set(indice.faltantes([t.referencia_vetorial for t in trechos]))
        reindexados = 0
        if faltantes:
            reindexados = indice.indexar(
                [_item_indice(t, t.citacao) for t in trechos if t.referencia_vetorial in faltantes]
            )
            log.info("%s: %d trecho(s) ausentes do indice foram repostos", sigla, reindexados)
        else:
            log.info("%s sem alteracao na fonte; indexacao dispensada", sigla)
        return ResultadoIngestao(sigla, len(trechos), reindexados, hash_fonte, alterada=False)

    if norma is None:
        norma = Norma(sigla=sigla, tipo=tipo, numero=numero, ano=ano, ementa=ementa)
        sessao.add(norma)
        sessao.flush()

    norma.ementa = ementa
    norma.url_fonte = url_fonte
    norma.hash_fonte = hash_fonte
    norma.data_indexacao = agora()
    vigente = bool(norma.vigente)   # reingerir nao reativa norma revogada (RN03)

    dispositivos = _sem_repeticao(segmentar(html, norma.referencia), sigla)
    if not dispositivos:
        raise ValueError(
            f"nenhum dispositivo reconhecido em {sigla}: confira se o HTML e o texto "
            "oficial do ato normativo"
        )

    # Substitui os trechos da norma: o texto oficial e a fonte da verdade.
    antigos = {
        t.referencia_vetorial: t
        for t in sessao.execute(
            select(TrechoNormativo).where(TrechoNormativo.id_norma == norma.id_norma)
        ).scalars()
    }
    obsoletos = set(antigos)

    itens_indice = []
    for dispositivo in dispositivos:
        referencia_vetorial = f"{sigla}|{dispositivo.referencia}"
        obsoletos.discard(referencia_vetorial)

        trecho = antigos.get(referencia_vetorial)
        if trecho is None:
            trecho = TrechoNormativo(
                id_norma=norma.id_norma, referencia_vetorial=referencia_vetorial
            )
            sessao.add(trecho)
        trecho.dispositivo = dispositivo.referencia
        trecho.texto = dispositivo.texto_completo
        trecho.texto_indexado = dispositivo.texto_indexado()
        trecho.artigo = dispositivo.artigo
        trecho.vigente = vigente
        itens_indice.append(_item_indice(trecho, dispositivo.citacao))

    for referencia in obsoletos:
        # Trecho que saiu do texto oficial: se alguma minuta o cita, ele fica no
        # banco (rastreabilidade) mas deixa de ser recuperavel.
        trecho = antigos[referencia]
        if trecho_citado(sessao, trecho):
            trecho.vigente = False
        else:
            sessao.delete(trecho)

    sessao.flush()
    indexados = indice.indexar(itens_indice)
    if obsoletos:
        indice.remover(sorted(obsoletos))
        log.info("%s: %d trecho(s) obsoleto(s) retirado(s) do indice", sigla, len(obsoletos))

    log.info("%s: %d dispositivos indexados", sigla, indexados)
    return ResultadoIngestao(sigla, len(dispositivos), indexados, hash_fonte, alterada=True)


def trecho_citado(sessao: Session, trecho: TrechoNormativo) -> bool:
    """Indica se alguma minuta esta vinculada ao trecho (RN02)."""
    from ..models import MinutaTrecho

    return (
        sessao.execute(
            select(MinutaTrecho.id_minuta).where(MinutaTrecho.id_trecho == trecho.id_trecho).limit(1)
        ).scalar_one_or_none()
        is not None
    )


def ingerir_arquivo(sessao: Session, caminho: str | Path, **metadados) -> ResultadoIngestao:
    """Atalho para ingerir um HTML ja baixado."""
    caminho = Path(caminho)
    return ingerir(sessao, bruto=caminho.read_bytes(), **metadados)


def reindexar_base(sessao: Session) -> int:
    """Reconstroi o indice vetorial inteiro a partir dos trechos do banco.

    Usado ao trocar o gerador de embeddings: os vetores antigos pertencem a outro
    espaco e nao podem ser reaproveitados.
    """
    indice = obter_indice()
    trechos = list(sessao.execute(select(TrechoNormativo)).scalars())
    if hasattr(indice, "limpar"):
        indice.limpar()
    total = indice.indexar([_item_indice(t, t.citacao) for t in trechos]) if trechos else 0
    log.info("Indice reconstruido: %d trechos", total)
    return total


__all__ = ["ingerir", "ingerir_arquivo", "reindexar_base", "ResultadoIngestao"]
