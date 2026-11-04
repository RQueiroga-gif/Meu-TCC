"""Indicadores agregados do uso do sistema (painel do gestor).

Consolida, a partir da trilha de auditoria e das tabelas do ciclo do DFD, os
numeros que alimentam o capitulo de resultados: ancoragem media das geracoes,
quantas geracoes o portao de suficiencia barrou, quantas versoes o agente
precisou ate aprovar e quanto tempo o ciclo levou.

Tudo e somente leitura e calculado sob demanda: o volume de um prototipo
avaliado em ambiente controlado dispensa tabelas de agregacao.
"""
from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import obter_sessao
from ..models import DocumentoDFD, Exportacao, Minuta, RegistroAuditoria, Usuario, Validacao
from ..security import PERFIL_GESTOR, exigir_perfil

router = APIRouter(prefix="/indicadores", tags=["Indicadores"])


def _media(valores: list[float]) -> float | None:
    return round(mean(valores), 4) if valores else None


@router.get("", summary="Indicadores de uso, qualidade e governança")
def indicadores(
    sessao: Session = Depends(obter_sessao),
    _: Usuario = Depends(exigir_perfil(PERFIL_GESTOR)),
) -> dict[str, Any]:
    # ---------------------------------------------------------------- DFDs
    situacoes = Counter(
        dict(
            sessao.execute(
                select(DocumentoDFD.situacao, func.count()).group_by(DocumentoDFD.situacao)
            ).all()
        )
    )
    total_dfds = sum(situacoes.values())

    # ------------------------------------------------------------ geracoes
    eventos = sessao.execute(
        select(RegistroAuditoria.acao, RegistroAuditoria.detalhe).where(
            RegistroAuditoria.acao.in_(
                ["GERACAO", "GERACAO_BARRADA", "FALHA_GERACAO", "EDICAO", "VALIDACAO"]
            )
        )
    ).all()
    por_acao = Counter(acao for acao, _ in eventos)
    geracoes = [d or {} for acao, d in eventos if acao == "GERACAO"]
    ancoragens = [float(d["ancoragem"]) for d in geracoes if d.get("ancoragem") is not None]
    prontidoes = [float(d["prontidao"]) for d in geracoes if d.get("prontidao") is not None]
    inconsistentes = sum(1 for d in geracoes if d.get("inconsistencia"))
    tempos = [float(d["tempo_ms"]) for d in geracoes if d.get("tempo_ms") is not None]
    modelos = Counter(d.get("modelo") or "?" for d in geracoes)
    tentativas = por_acao["GERACAO"] + por_acao["GERACAO_BARRADA"] + por_acao["FALHA_GERACAO"]

    # --------------------------------------------------------- validacoes
    decisoes = Counter(
        dict(
            sessao.execute(
                select(Validacao.decisao, func.count()).group_by(Validacao.decisao)
            ).all()
        )
    )

    # ---------------------------------------- versoes e tempo ate aprovar
    versoes_por_dfd = [
        v for (v,) in sessao.execute(
            select(func.max(Minuta.versao)).group_by(Minuta.id_dfd)
        ).all()
    ]
    primeira_aprovacao = dict(
        sessao.execute(
            select(Validacao.id_dfd, func.min(Validacao.data_validacao))
            .where(Validacao.decisao == "aprovado")
            .group_by(Validacao.id_dfd)
        ).all()
    )
    criacao = dict(
        sessao.execute(
            select(DocumentoDFD.id_dfd, DocumentoDFD.data_criacao).where(
                DocumentoDFD.id_dfd.in_(list(primeira_aprovacao))
            )
        ).all()
    ) if primeira_aprovacao else {}
    horas_ate_aprovar = [
        (primeira_aprovacao[i] - criacao[i]).total_seconds() / 3600
        for i in primeira_aprovacao
        if i in criacao
    ]
    edicoes_humanas = sessao.execute(
        select(func.count()).select_from(Minuta).where(Minuta.origem == "edicao")
    ).scalar_one()

    return {
        "dfds": {
            "total": total_dfds,
            "por_situacao": dict(situacoes),
            "exportados": sessao.execute(
                select(func.count(func.distinct(Exportacao.id_dfd)))
            ).scalar_one(),
        },
        "geracao": {
            "tentativas": tentativas,
            "concluidas": por_acao["GERACAO"],
            "barradas_por_suficiencia": por_acao["GERACAO_BARRADA"],
            "falhas_do_provedor": por_acao["FALHA_GERACAO"],
            "taxa_sucesso": round(por_acao["GERACAO"] / tentativas, 4) if tentativas else None,
            "ancoragem_media": _media(ancoragens),
            "ancoragem_minima": round(min(ancoragens), 4) if ancoragens else None,
            "prontidao_media": _media(prontidoes),
            "com_inconsistencia": inconsistentes,
            "taxa_inconsistencia": round(inconsistentes / len(geracoes), 4) if geracoes else None,
            "por_modelo": dict(modelos),
            "tempo_mediano_ms": round(median(tempos), 1) if tempos else None,
        },
        "revisao": {
            "edicoes_humanas": edicoes_humanas,
            "versoes_media_por_dfd": _media([float(v) for v in versoes_por_dfd]),
            "versoes_maxima": max(versoes_por_dfd) if versoes_por_dfd else None,
        },
        "validacao": {
            "aprovacoes": decisoes.get("aprovado", 0),
            "devolucoes": decisoes.get("devolvido", 0),
            "horas_ate_aprovar_mediana": round(median(horas_ate_aprovar), 2)
            if horas_ate_aprovar else None,
        },
    }
