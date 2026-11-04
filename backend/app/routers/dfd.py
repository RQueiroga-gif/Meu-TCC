"""Rotas do ciclo do DFD: geracao, revisao, correcao, validacao e exportacao.

UC04 a UC08. A trava da RN01 tem tres camadas: o gatilho do banco (que nenhum
caminho contorna), a verificacao desta rota e o botao desabilitado na interface.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..config import config
from ..core import auditoria
from ..core.anonimizador import minimizar
from ..core.indice import IndiceIncompativel
from ..database import obter_sessao
from ..llm.base import ErroProvedorLLM
from ..models import DocumentoDFD, Exportacao, Minuta, Usuario, Validacao
from ..schemas import (
    CAMPOS_ART_8,
    CampoCorrigidoSaida,
    CorrecaoEntrada,
    CorrecaoSaida,
    ExportacaoSaida,
    GeracaoEntrada,
    MinutaSaida,
    RevisaoEntrada,
    TrechoSaida,
    ValidacaoEntrada,
    ValidacaoSaida,
)
from ..security import PERFIL_GESTOR, usuario_atual
from ..services import correcao
from ..services.exportador import gerar_docx
from ..services.geracao import FundamentoInsuficiente, Orquestrador, ResultadoGeracao, avaliar
from .demandas import obter_dfd_do_usuario

log = logging.getLogger(__name__)
router = APIRouter(prefix="/dfd", tags=["Documento de Formalização da Demanda"])

MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MENSAGEM_RN01 = (
    "Este documento ainda não possui validação humana registrada para a versão "
    "atual. A exportação só é liberada após a aprovação."
)


def pasta_saida() -> Path:
    return Path(config.PASTA_SAIDA)


def _versao_aprovada(sessao: Session, minuta: Minuta) -> bool:
    """A decisao mais recente sobre esta versao e de aprovacao.

    Uma devolucao posterior a uma aprovacao revoga a aprovacao na aplicacao.
    """
    ultima = sessao.execute(
        select(Validacao.decisao)
        .where(Validacao.id_minuta == minuta.id_minuta)
        .order_by(Validacao.id_validacao.desc())
        .limit(1)
    ).scalar_one_or_none()
    return ultima == "aprovado"


def _montar_saida(
    sessao: Session, dfd: DocumentoDFD, resultado: ResultadoGeracao, *, criada: bool = True
) -> MinutaSaida:
    minuta = resultado.minuta
    demanda = dfd.demanda
    return MinutaSaida(
        id_minuta=minuta.id_minuta,
        id_dfd=minuta.id_dfd,
        numero=dfd.numero,
        titulo=demanda.titulo,
        unidade_requisitante=demanda.unidade_requisitante,
        responsavel=demanda.usuario.nome,
        situacao=dfd.situacao,
        aprovada=_versao_aprovada(sessao, minuta),
        versao=minuta.versao,
        conteudo=minuta.conteudo,
        origem=minuta.origem,
        modelo_llm=minuta.modelo_llm,
        versao_prompt=minuta.versao_prompt,
        data_geracao=minuta.data_geracao,
        fontes=[
            TrechoSaida(
                marcador=f.get("marcador", f"T{i}"),
                referencia=f.get("referencia", ""),
                texto=f.get("texto", ""),
                similaridade=f.get("similaridade", 0.0),
            )
            for i, f in enumerate(resultado.fontes, start=1)
        ],
        verificacao=resultado.verificacao,
        pendencias=resultado.pendencias,
        analise=resultado.analise,
        desempenho=resultado.desempenho,
        versao_criada=criada,
    )


def _exigir_editavel(dfd: DocumentoDFD, acao: str) -> None:
    if dfd.situacao == "exportado":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Este DFD já foi exportado e não admite {acao}.",
        )


def _registrar_isolado(sessao: Session, usuario: Usuario, id_dfd: int, acao: str,
                       detalhe: dict[str, Any]) -> None:
    """Registra um evento em transacao propria, apos o rollback da acao (WF-99)."""
    try:
        auditoria.registrar(
            sessao, id_usuario=usuario.id_usuario, id_dfd=id_dfd, acao=acao, detalhe=detalhe
        )
        sessao.commit()
    except Exception:  # noqa: BLE001
        sessao.rollback()
        log.exception("Não foi possível auditar o evento %s", acao)


# ------------------------------------------------------------------ geração
@router.post("/{id_dfd}/gerar", response_model=MinutaSaida, summary="Gerar minuta (UC04)")
def gerar(
    id_dfd: int,
    parametros: GeracaoEntrada | None = None,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> MinutaSaida:
    """Recupera os fundamentos, gera a minuta e grava a versão."""
    parametros = parametros or GeracaoEntrada()
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    _exigir_editavel(dfd, "nova geração")

    experimental = parametros.provedor is not None or not parametros.usar_rag
    if experimental and usuario.perfil != PERFIL_GESTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Troca de provedor e geração sem RAG são restritas aos experimentos (gestor).",
        )

    try:
        resultado = Orquestrador(sessao).gerar(
            dfd=dfd,
            usuario=usuario,
            top_k=parametros.top_k,
            limiar=parametros.limiar,
            provedor=parametros.provedor,
            usar_rag=parametros.usar_rag,
        )
    except FundamentoInsuficiente as erro:
        sessao.rollback()
        _registrar_isolado(
            sessao, usuario, id_dfd, "GERACAO_BARRADA",
            {"limiar": erro.limiar, "descartados": erro.descartados},
        )
        # Fluxo alternativo A1: o sistema informa em vez de inventar.
        raise HTTPException(
            status_code=422,
            detail=(
                "Não foram encontrados fundamentos normativos suficientes para esta "
                "demanda. Descreva a necessidade com mais detalhes, informando o objeto "
                "e a finalidade da contratação."
            ),
        ) from erro
    except IndiceIncompativel as erro:
        sessao.rollback()
        log.error("Índice vetorial incompatível: %s", erro)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A base normativa precisa ser reindexada pelo gestor antes de gerar minutas.",
        ) from erro
    except ErroProvedorLLM as erro:
        sessao.rollback()
        _registrar_isolado(sessao, usuario, id_dfd, "FALHA_GERACAO", {"erro": str(erro)[:500]})
        # Fluxo alternativo A2.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "O serviço de geração está temporariamente indisponível. A demanda "
                "foi preservada e nenhum dado foi perdido."
            ),
        ) from erro

    sessao.commit()
    return _montar_saida(sessao, dfd, resultado)


# ------------------------------------------------------------------ revisão
@router.post("/{id_dfd}/revisar", response_model=MinutaSaida, summary="Gravar revisão (UC05)")
def revisar(
    id_dfd: int,
    dados: RevisaoEntrada,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> MinutaSaida:
    """Grava a minuta editada como nova versão, sem sobrescrever a anterior.

    Se o conteúdo não mudou, nenhuma versão é criada e a atual é devolvida
    (`versao_criada = false`).
    """
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    _exigir_editavel(dfd, "edição")

    resultado = Orquestrador(sessao).revisar(
        dfd=dfd,
        usuario=usuario,
        conteudo=dados.conteudo,
        correcao_ia=dados.correcao_ia.model_dump() if dados.correcao_ia else None,
    )
    if resultado is None:
        atual = dfd.versao_atual
        return _montar_saida(sessao, dfd, avaliar(atual, dfd.demanda.descricao), criada=False)
    sessao.commit()
    return _montar_saida(sessao, dfd, resultado)


@router.post("/{id_dfd}/corrigir", response_model=CorrecaoSaida, summary="Sugerir revisão de redação")
def corrigir(
    id_dfd: int,
    dados: CorrecaoEntrada | None = None,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> CorrecaoSaida:
    """Pede a um segundo modelo a revisão de redação dos campos I e II.

    Devolve apenas a sugestão: nenhuma versão é criada. Se o agente aceitar, a
    interface grava o texto sugerido por `/revisar`, informando `correcao_ia`.
    """
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    _exigir_editavel(dfd, "revisão de redação")
    conteudo = dados.conteudo if dados and dados.conteudo else None
    if conteudo is None:
        atual = dfd.versao_atual
        if atual is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Este DFD ainda não tem minuta."
            )
        conteudo = atual.conteudo or {}

    try:
        sugestao = correcao.sugerir(conteudo)
    except ErroProvedorLLM as erro:
        _registrar_isolado(sessao, usuario, id_dfd, "FALHA_CORRECAO", {"erro": str(erro)[:500]})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="O serviço de revisão de texto está temporariamente indisponível. "
            "A minuta não foi alterada.",
        ) from erro

    _registrar_isolado(
        sessao, usuario, id_dfd, "CORRECAO_SUGERIDA",
        {
            "provedor": sugestao.provedor,
            "modelo": sugestao.modelo,
            "alterados": [c.campo for c in sugestao.campos if c.alterado],
            "descartados": [c.campo for c in sugestao.campos if c.motivo_descarte],
            "tempo_ms": sugestao.parametros.get("tempo_ms"),
        },
    )
    return CorrecaoSaida(
        provedor=sugestao.provedor,
        modelo=sugestao.modelo,
        campos=[
            CampoCorrigidoSaida(
                campo=c.campo,
                original=c.original,
                sugerido=c.sugerido,
                alterado=c.alterado,
                motivo_descarte=c.motivo_descarte,
            )
            for c in sugestao.campos
        ],
        observacoes=sugestao.observacoes,
        conteudo_sugerido=sugestao.aplicar(conteudo),
    )


# ------------------------------------------------------------------ validação
@router.post("/{id_dfd}/validar", response_model=ValidacaoSaida, summary="Validar (UC07)")
def validar(
    id_dfd: int,
    dados: ValidacaoEntrada,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> ValidacaoSaida:
    """Registra a decisão humana, vinculada à versão exata avaliada (RN01, RN04)."""
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    _exigir_editavel(dfd, "nova validação")

    minuta = sessao.get(Minuta, dados.id_minuta)
    if minuta is None or minuta.id_dfd != dfd.id_dfd:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Versão da minuta não encontrada."
        )

    atual = dfd.versao_atual
    if atual is not None and minuta.id_minuta != atual.id_minuta:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A minuta foi alterada: você avaliou a versão {minuta.versao}, "
                f"mas a atual é a {atual.versao}. Revise a versão mais recente."
            ),
        )

    # RF07 garantido na API, e nao apenas na interface: aprovar exige a
    # confirmacao individual de cada campo do art. 8º.
    if dados.decisao == "aprovado":
        faltantes = [c for c in CAMPOS_ART_8 if c not in dados.campos_confirmados]
        if faltantes:
            raise HTTPException(
                status_code=422,
                detail="Confirme todos os campos do art. 8º antes de aprovar. Faltam: "
                + ", ".join(faltantes) + ".",
            )
    elif not (dados.justificativa or "").strip():
        raise HTTPException(
            status_code=422,
            detail="Informe o motivo da devolução para orientar os ajustes.",
        )

    justificativa = minimizar(dados.justificativa).texto if dados.justificativa else None
    validacao = Validacao(
        id_dfd=dfd.id_dfd,
        id_minuta=minuta.id_minuta,
        id_usuario=usuario.id_usuario,
        decisao=dados.decisao,
        justificativa=justificativa,
    )
    sessao.add(validacao)
    if dados.decisao == "devolvido":
        dfd.situacao = "em_elaboracao"
    sessao.flush()

    auditoria.registrar(
        sessao,
        id_usuario=usuario.id_usuario,
        id_dfd=dfd.id_dfd,
        acao="VALIDACAO",
        detalhe={
            "versao": minuta.versao,
            "decisao": dados.decisao,
            "campos_confirmados": dados.campos_confirmados,
        },
    )
    sessao.commit()

    return ValidacaoSaida(
        id_validacao=validacao.id_validacao,
        id_dfd=validacao.id_dfd,
        id_minuta=validacao.id_minuta,
        decisao=validacao.decisao,
        responsavel=usuario.nome,
        data_validacao=validacao.data_validacao,
    )


# ------------------------------------------------------------------ exportação
@router.post("/{id_dfd}/exportar", response_model=ExportacaoSaida, summary="Exportar (UC08)")
def exportar(
    id_dfd: int,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> ExportacaoSaida:
    """Gera o .docx do DFD aprovado.

    A regra RN01 é aplicada pelo gatilho do banco **antes** de o arquivo existir:
    o registro da exportação é inserido primeiro, e só se o banco o aceitar o
    documento é produzido. Cada exportação tem arquivo próprio, de modo que o
    hash gravado continua valendo para o arquivo que o originou.
    """
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    minuta = dfd.versao_atual
    if minuta is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Não há minuta para exportar. Gere o documento antes.",
        )
    if not _versao_aprovada(sessao, minuta):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MENSAGEM_RN01)

    registro = Exportacao(
        id_dfd=dfd.id_dfd,
        id_minuta=minuta.id_minuta,
        id_usuario=usuario.id_usuario,
        formato="docx",
    )
    sessao.add(registro)
    try:
        sessao.flush()          # gatilho RN01 decide aqui
    except (IntegrityError, OperationalError) as erro:
        sessao.rollback()
        if "RN01" in str(erro.orig or erro):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MENSAGEM_RN01) from erro
        raise

    caminho = pasta_saida() / (
        f"dfd_{dfd.id_dfd:04d}_v{minuta.versao}_exp{registro.id_exportacao}.docx"
    )
    try:
        gerar_docx(dfd=dfd, minuta=minuta, destino=caminho, emitido_por=usuario.nome)
    except Exception:
        sessao.rollback()
        caminho.unlink(missing_ok=True)
        raise
    registro.hash_arquivo = hashlib.sha256(caminho.read_bytes()).hexdigest()
    dfd.demanda.situacao = "concluida"

    auditoria.registrar(
        sessao,
        id_usuario=usuario.id_usuario,
        id_dfd=dfd.id_dfd,
        acao="EXPORTACAO",
        detalhe={
            "versao": minuta.versao,
            "hash": registro.hash_arquivo,
            "formato": "docx",
            "id_exportacao": registro.id_exportacao,
        },
    )
    try:
        sessao.commit()
    except Exception:
        sessao.rollback()
        caminho.unlink(missing_ok=True)
        raise

    return ExportacaoSaida(
        id_exportacao=registro.id_exportacao,
        id_dfd=dfd.id_dfd,
        id_minuta=minuta.id_minuta,
        formato="docx",
        hash_arquivo=registro.hash_arquivo,
        arquivo=caminho.name,
        url_download=f"/dfd/{dfd.id_dfd}/exportacoes/{registro.id_exportacao}/arquivo",
        data_hora=registro.data_hora,
    )


@router.get("/{id_dfd}/exportacoes", summary="Exportações do DFD")
def exportacoes(
    id_dfd: int,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> list[dict[str, Any]]:
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    registros = sessao.execute(
        select(Exportacao, Minuta.versao)
        .join(Minuta, Exportacao.id_minuta == Minuta.id_minuta)
        .where(Exportacao.id_dfd == dfd.id_dfd)
        .order_by(Exportacao.id_exportacao.desc())
    ).all()
    return [
        {
            "id_exportacao": e.id_exportacao,
            "versao": versao,
            "formato": e.formato,
            "hash_arquivo": e.hash_arquivo,
            "data_hora": e.data_hora,
            "url_download": f"/dfd/{dfd.id_dfd}/exportacoes/{e.id_exportacao}/arquivo",
        }
        for e, versao in registros
    ]


@router.get(
    "/{id_dfd}/exportacoes/{id_exportacao}/arquivo",
    summary="Baixar o .docx exportado",
    response_class=FileResponse,
)
def baixar(
    id_dfd: int,
    id_exportacao: int,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> FileResponse:
    """Entrega o arquivo exatamente como exportado, conferindo o hash gravado."""
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    registro = sessao.get(Exportacao, id_exportacao)
    if registro is None or registro.id_dfd != dfd.id_dfd:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exportação não encontrada.")

    versao = sessao.get(Minuta, registro.id_minuta).versao
    caminho = pasta_saida() / f"dfd_{dfd.id_dfd:04d}_v{versao}_exp{registro.id_exportacao}.docx"
    if not caminho.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="O arquivo desta exportação não está mais disponível. Exporte novamente.",
        )
    if hashlib.sha256(caminho.read_bytes()).hexdigest() != registro.hash_arquivo:
        log.error("Hash divergente no arquivo exportado %s", caminho)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="O arquivo não corresponde ao hash registrado na exportação.",
        )
    return FileResponse(
        caminho,
        media_type=MIME_DOCX,
        filename=f"{(dfd.numero or f'DFD {dfd.id_dfd}').replace('/', '-').replace(' ', '_')}.docx",
    )


# ------------------------------------------------------------------ consulta
@router.get("/{id_dfd}", response_model=MinutaSaida, summary="Versão atual do DFD")
def obter(
    id_dfd: int,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> MinutaSaida:
    """Devolve a minuta corrente com fontes, verificação e análise recalculadas."""
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    minuta = dfd.versao_atual
    if minuta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Este DFD ainda não tem minuta."
        )
    return _montar_saida(sessao, dfd, avaliar(minuta, dfd.demanda.descricao))


@router.get("/{id_dfd}/versoes", summary="Histórico de versões")
def versoes(
    id_dfd: int,
    sessao: Session = Depends(obter_sessao),
    usuario: Usuario = Depends(usuario_atual),
) -> list[dict[str, Any]]:
    """Histórico completo: nada é sobrescrito."""
    dfd = obter_dfd_do_usuario(id_dfd, sessao, usuario)
    minutas = sessao.execute(
        select(Minuta, Usuario.nome)
        .join(Usuario, Minuta.id_usuario == Usuario.id_usuario)
        .where(Minuta.id_dfd == dfd.id_dfd)
        .order_by(Minuta.versao)
    ).all()
    validacoes = {
        v.id_minuta: v.decisao
        for v in sessao.execute(
            select(Validacao)
            .where(Validacao.id_dfd == dfd.id_dfd)
            .order_by(Validacao.id_validacao)
        ).scalars()
    }
    return [
        {
            "id_minuta": m.id_minuta,
            "versao": m.versao,
            "origem": m.origem,
            "autor": nome,
            "modelo_llm": m.modelo_llm,
            "versao_prompt": m.versao_prompt,
            "data_geracao": m.data_geracao,
            "decisao": validacoes.get(m.id_minuta),
        }
        for m, nome in minutas
    ]
