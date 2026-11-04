"""Rotas de demanda e de consulta a DFDs (UC02, RF02)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import auditoria
from ..core.anonimizador import minimizar
from ..database import obter_sessao
from ..models import Demanda, DocumentoDFD, Minuta, Usuario, agora
from ..schemas import DemandaEntrada, DemandaSaida, DFDResumo
from ..security import PERFIL_GESTOR, usuario_atual

log = logging.getLogger(__name__)
router = APIRouter(prefix="/demandas", tags=["Demandas"])


def _saida(demanda: Demanda, id_dfd: int, removidos: dict[str, int] | None = None) -> DemandaSaida:
    return DemandaSaida(
        id_demanda=demanda.id_demanda,
        id_dfd=id_dfd,
        unidade_requisitante=demanda.unidade_requisitante,
        titulo=demanda.titulo,
        descricao=demanda.descricao,
        situacao=demanda.situacao,
        data_registro=demanda.data_registro,
        dados_pessoais_removidos=removidos or {},
    )


@router.post(
    "",
    response_model=DemandaSaida,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar demanda e abrir o DFD",
)
def registrar(
    dados: DemandaEntrada,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> DemandaSaida:
    """Registra a necessidade administrativa e cria o DFD correspondente.

    A descrição é gravada **já minimizada** (RN06): identificadores diretos são
    substituídos por marcadores antes de qualquer persistência ou envio externo.
    """
    resultado = minimizar(dados.descricao)
    titulo = minimizar(dados.titulo)
    removidos = dict(resultado.ocorrencias)
    for rotulo, qtd in titulo.ocorrencias.items():
        removidos[rotulo] = removidos.get(rotulo, 0) + qtd
    if removidos:
        log.info(
            "Demanda de %s: %d identificador(es) removido(s) antes da gravação",
            usuario.email,
            sum(removidos.values()),
        )

    demanda = Demanda(
        id_usuario=usuario.id_usuario,
        unidade_requisitante=dados.unidade_requisitante,
        titulo=titulo.texto,
        descricao=resultado.texto,
        situacao="em_elaboracao",
    )
    sessao.add(demanda)
    sessao.flush()

    agora_dt = agora()
    dfd = DocumentoDFD(
        id_demanda=demanda.id_demanda,
        exercicio=agora_dt.year,
        situacao="em_elaboracao",
    )
    sessao.add(dfd)
    sessao.flush()
    dfd.numero = f"DFD {dfd.id_dfd:04d}/{agora_dt.year}"

    auditoria.registrar(
        sessao,
        id_usuario=usuario.id_usuario,
        id_dfd=dfd.id_dfd,
        acao="REGISTRO_DEMANDA",
        detalhe={
            "unidade": demanda.unidade_requisitante,
            "dados_pessoais_removidos": removidos,
        },
    )
    sessao.commit()
    return _saida(demanda, dfd.id_dfd, removidos)


@router.get("", response_model=list[DFDResumo], summary="Listar DFDs do usuário")
def listar(
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> list[DFDResumo]:
    """Lista os DFDs do usuário autenticado (o gestor vê todos)."""
    ultima_versao = (
        select(Minuta.id_dfd, func.max(Minuta.versao).label("versao"))
        .group_by(Minuta.id_dfd)
        .subquery()
    )
    consulta = (
        select(DocumentoDFD, Demanda, Usuario.nome, ultima_versao.c.versao)
        .join(Demanda, DocumentoDFD.id_demanda == Demanda.id_demanda)
        .join(Usuario, Demanda.id_usuario == Usuario.id_usuario)
        .outerjoin(ultima_versao, ultima_versao.c.id_dfd == DocumentoDFD.id_dfd)
    )
    if usuario.perfil != PERFIL_GESTOR:
        consulta = consulta.where(Demanda.id_usuario == usuario.id_usuario)
    consulta = consulta.order_by(DocumentoDFD.id_dfd.desc())

    return [
        DFDResumo(
            id_dfd=dfd.id_dfd,
            numero=dfd.numero,
            titulo=demanda.titulo,
            unidade_requisitante=demanda.unidade_requisitante,
            responsavel=nome,
            situacao=dfd.situacao,
            versao_atual=versao,
            data_criacao=dfd.data_criacao,
        )
        for dfd, demanda, nome, versao in sessao.execute(consulta).all()
    ]


def obter_dfd_do_usuario(
    id_dfd: int, sessao: Session, usuario: Usuario
) -> DocumentoDFD:
    """Recupera o DFD, garantindo que pertence ao usuário (ou que é gestor)."""
    dfd = sessao.get(DocumentoDFD, id_dfd)
    if dfd is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DFD não encontrado.")
    if usuario.perfil != PERFIL_GESTOR and dfd.demanda.id_usuario != usuario.id_usuario:
        # 404 em vez de 403: nao revela a existencia de documentos de terceiros.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DFD não encontrado.")
    return dfd


@router.get("/{id_dfd}", response_model=DemandaSaida, summary="Consultar a demanda de um DFD")
def obter(
    id_dfd: int,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> DemandaSaida:
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    return _saida(dfd.demanda, dfd.id_dfd)


@router.put("/{id_dfd}", response_model=DemandaSaida, summary="Ajustar a demanda (fluxo A1)")
def ajustar(
    id_dfd: int,
    dados: DemandaEntrada,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> DemandaSaida:
    """Reescreve a demanda do mesmo DFD, sem abrir um documento novo.

    Usado quando o portão de suficiência barra a geração (fluxo A1) ou quando o
    agente quer detalhar a necessidade antes de gerar de novo. As minutas já
    geradas permanecem no histórico.
    """
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    if dfd.situacao == "exportado":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este DFD já foi exportado e não admite alteração da demanda.",
        )

    resultado = minimizar(dados.descricao)
    titulo = minimizar(dados.titulo)
    removidos = dict(resultado.ocorrencias)
    for rotulo, qtd in titulo.ocorrencias.items():
        removidos[rotulo] = removidos.get(rotulo, 0) + qtd

    demanda = dfd.demanda
    demanda.unidade_requisitante = dados.unidade_requisitante
    demanda.titulo = titulo.texto
    demanda.descricao = resultado.texto

    auditoria.registrar(
        sessao,
        id_usuario=usuario.id_usuario,
        id_dfd=dfd.id_dfd,
        acao="AJUSTE_DEMANDA",
        detalhe={"dados_pessoais_removidos": removidos},
    )
    sessao.commit()
    return _saida(demanda, dfd.id_dfd, removidos)
