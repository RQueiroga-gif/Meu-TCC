"""Ponto de entrada da API.

Executar em desenvolvimento:
    uvicorn app.main:app --reload --port 8001

Documentacao interativa: http://localhost:8001/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from .config import config
from .core.embeddings import obter_embedder
from .core.indice import obter_indice
from .database import aplicar_esquema, saude
from .llm.factory import obter_provedor
from .routers import auth, demandas, dfd, indicadores, normas

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dfd")


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    """Prepara o banco na subida e registra a configuracao efetiva."""
    aplicar_esquema()
    if config.chave_insegura:
        log.warning(
            "SECRET_KEY padrão em uso: qualquer pessoa pode forjar tokens. Defina uma "
            "chave própria no .env (python -c \"import secrets; print(secrets.token_hex(32))\")."
        )
    log.info(
        "Banco: %s | Índice: %s (%d trechos) | Provedor de LLM: %s | Revisão de texto: %s",
        config.DB_DIALETO,
        config.INDICE_BACKEND,
        obter_indice().total(),
        config.LLM_PROVEDOR,
        config.LLM_PROVEDOR_CORRECAO or config.LLM_PROVEDOR,
    )
    yield
    log.info("Encerrando a aplicação.")


app = FastAPI(
    title=config.APP_NOME,
    version=config.APP_VERSAO,
    lifespan=ciclo_de_vida,
    description=(
        "Protótipo de automação assistida do Documento de Formalização da Demanda "
        "(DFD), com recuperação normativa, geração condicionada e supervisão humana "
        "obrigatória. TCC — Ciência da Computação."
    ),
    contact={"name": "Ronan Rocha Queiroga"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(demandas.router)
app.include_router(dfd.router)
app.include_router(normas.router)
app.include_router(normas.router_auditoria)
app.include_router(indicadores.router)


@app.exception_handler(IntegrityError)
async def conflito(request: Request, erro: IntegrityError) -> JSONResponse:
    """Violacao de unicidade quase sempre e concorrencia (duas abas, duplo clique)."""
    log.warning("Conflito de integridade em %s %s: %s", request.method, request.url.path, erro.orig)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": "Outra operação alterou este registro ao mesmo tempo. "
            "Recarregue a página e tente novamente."
        },
    )


@app.exception_handler(Exception)
async def erro_nao_tratado(request: Request, erro: Exception) -> JSONResponse:
    """Nenhum detalhe interno vaza para o cliente; o rastro fica no log."""
    log.exception("Erro não tratado em %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Erro interno. A ocorrência foi registrada."},
    )


@app.get("/saude", tags=["Infraestrutura"], summary="Verificação de disponibilidade")
def verificar_saude() -> dict[str, object]:
    banco_ok = saude()
    try:
        provedor = obter_provedor().nome
    except Exception:  # noqa: BLE001
        provedor = "indisponível"
    embedder = obter_embedder()
    return {
        "aplicacao": config.APP_NOME,
        "versao": config.APP_VERSAO,
        "banco": {"dialeto": config.DB_DIALETO, "disponivel": banco_ok},
        "indice": {"backend": config.INDICE_BACKEND, "trechos": obter_indice().total()},
        "embeddings": {"gerador": embedder.identificador, "semantico": embedder.semantico},
        "provedor_llm": provedor,
        "provedor_correcao": config.LLM_PROVEDOR_CORRECAO or provedor,
    }
