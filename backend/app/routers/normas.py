"""Rotas de gestao da base normativa (UC10, UC11, RF11, RF12) e de auditoria (UC09)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from ..core import auditoria
from ..core.indice import IndiceIncompativel, obter_indice
from ..core.recuperacao import limiar_padrao, recuperar
from ..database import obter_sessao
from ..models import Demanda, DocumentoDFD, Norma, RegistroAuditoria, TrechoNormativo, Usuario
from ..schemas import AuditoriaSaida, IntegridadeSaida, NormaSaida
from ..security import PERFIL_GESTOR, exigir_perfil, usuario_atual
from ..services.ingestao import reindexar_base

log = logging.getLogger(__name__)

router = APIRouter(prefix="/normas", tags=["Base normativa"])
router_auditoria = APIRouter(prefix="/auditoria", tags=["Auditoria"])


@router.get("", response_model=list[NormaSaida], summary="Listar normas indexadas")
def listar(
    sessao: Session = Depends(obter_sessao),
    _: Usuario = Depends(usuario_atual),
) -> list[Norma]:
    return list(sessao.execute(select(Norma).order_by(Norma.ano.desc())).scalars())


@router.get("/estatisticas", summary="Situação da base normativa")
def estatisticas(
    sessao: Session = Depends(obter_sessao),
    _: Usuario = Depends(usuario_atual),
) -> dict[str, Any]:
    """Números exibidos no painel do gestor."""
    normas_total, normas_vigentes, ultima = sessao.execute(
        select(
            func.count(Norma.id_norma),
            func.coalesce(func.sum(cast(Norma.vigente, Integer)), 0),
            func.max(Norma.data_indexacao),
        )
    ).one()
    trechos, trechos_vigentes = sessao.execute(
        select(
            func.count(TrechoNormativo.id_trecho),
            func.coalesce(func.sum(cast(TrechoNormativo.vigente, Integer)), 0),
        )
    ).one()
    return {
        "normas_total": int(normas_total),
        "normas_vigentes": int(normas_vigentes),
        "trechos_indexados": int(trechos),
        "trechos_vigentes": int(trechos_vigentes),
        "trechos_no_indice": obter_indice().total(),
        "ultima_indexacao": ultima,
        "limiar_efetivo": limiar_padrao(),
    }


@router.post("/{id_norma}/vigencia", summary="Ativar ou desativar norma (RN03)")
def alterar_vigencia(
    id_norma: int,
    vigente: bool = Query(..., description="true mantém a norma disponível para recuperação"),
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(exigir_perfil(PERFIL_GESTOR)),
) -> dict[str, Any]:
    """Desativar uma norma a remove imediatamente das recuperações (RN03)."""
    norma = sessao.get(Norma, id_norma)
    if norma is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Norma não encontrada.")

    norma.vigente = vigente
    trechos = list(
        sessao.execute(
            select(TrechoNormativo).where(TrechoNormativo.id_norma == id_norma)
        ).scalars()
    )
    for trecho in trechos:
        trecho.vigente = vigente

    auditoria.registrar(
        sessao,
        id_usuario=usuario.id_usuario,
        acao="ALTERACAO_VIGENCIA",
        detalhe={"norma": norma.sigla, "vigente": vigente, "trechos": len(trechos)},
    )
    sessao.commit()
    # Indice atualizado depois do commit: se o banco falhar, o indice nao muda.
    # Apenas a marca de vigencia muda — nenhum vetor e recalculado.
    obter_indice().atualizar_vigencia([t.referencia_vetorial for t in trechos], vigente)
    return {"sigla": norma.sigla, "vigente": vigente, "trechos_afetados": len(trechos)}


@router.post("/reindexar", summary="Reconstruir o índice vetorial a partir do banco")
def reindexar(
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(exigir_perfil(PERFIL_GESTOR)),
) -> dict[str, Any]:
    """Recalcula todos os vetores a partir dos trechos gravados no banco.

    Necessário ao trocar o gerador de embeddings (por exemplo, ao instalar o
    BGE-M3) ou quando o painel acusa banco e índice dessincronizados.
    """
    total = reindexar_base(sessao)
    auditoria.registrar(
        sessao, id_usuario=usuario.id_usuario, acao="REINDEXACAO", detalhe={"trechos": total}
    )
    sessao.commit()
    return {"trechos_indexados": total, "trechos_no_indice": obter_indice().total()}


@router.get("/buscar", summary="Consultar a base normativa (diagnóstico)")
def buscar(
    consulta: str = Query(min_length=3, max_length=2_000),
    k: int = Query(default=6, ge=1, le=20),
    limiar: float = Query(default=0.0, ge=0.0, le=1.0),
    _: Usuario = Depends(usuario_atual),
) -> dict[str, Any]:
    """Executa a recuperação isoladamente — usada na calibração do limiar (F2)."""
    try:
        resultado = recuperar(consulta, k=k, limiar=limiar)
    except IndiceIncompativel as erro:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(erro)) from erro
    return {
        "suficiente": resultado.suficiente,
        "limiar": resultado.limiar,
        "limiar_padrao": limiar_padrao(),
        "descartados": resultado.descartados,
        "trechos": resultado.contexto_para_dict(),
    }


# --------------------------------------------------------------- auditoria
@router_auditoria.get("", response_model=list[AuditoriaSaida], summary="Consultar registros")
def consultar(
    id_dfd: int | None = Query(default=None),
    acao: str | None = Query(default=None, max_length=60),
    limite: int = Query(default=100, ge=1, le=1_000),
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> list[AuditoriaSaida]:
    """O gestor vê toda a trilha; o demandante vê seus atos e os de seus DFDs."""
    consulta = (
        select(RegistroAuditoria, Usuario.nome)
        .join(Usuario, RegistroAuditoria.id_usuario == Usuario.id_usuario)
        .order_by(RegistroAuditoria.id_registro.desc())
    )
    if usuario.perfil != PERFIL_GESTOR:
        meus_dfds = (
            select(DocumentoDFD.id_dfd)
            .join(Demanda, DocumentoDFD.id_demanda == Demanda.id_demanda)
            .where(Demanda.id_usuario == usuario.id_usuario)
        )
        consulta = consulta.where(
            or_(
                RegistroAuditoria.id_usuario == usuario.id_usuario,
                RegistroAuditoria.id_dfd.in_(meus_dfds),
            )
        )
    if id_dfd is not None:
        consulta = consulta.where(RegistroAuditoria.id_dfd == id_dfd)
    if acao:
        consulta = consulta.where(RegistroAuditoria.acao == acao.upper())

    return [
        AuditoriaSaida(
            id_registro=r.id_registro,
            id_usuario=r.id_usuario,
            usuario_nome=nome,
            id_dfd=r.id_dfd,
            acao=r.acao,
            detalhe=r.detalhe,
            data_hora=r.data_hora,
            hash_registro=r.hash_registro,
        )
        for r, nome in sessao.execute(consulta.limit(limite)).all()
    ]


@router_auditoria.get(
    "/integridade", response_model=IntegridadeSaida, summary="Verificar a cadeia de hashes"
)
def integridade(
    sessao: Session = Depends(obter_sessao),
    _: Usuario = Depends(exigir_perfil(PERFIL_GESTOR)),
) -> dict[str, Any]:
    """Recalcula a cadeia inteira: qualquer adulteração quebra o encadeamento."""
    return auditoria.verificar_cadeia(sessao)
