"""Autenticacao e controle de acesso por perfil (RF01, RNF05).

Senhas nunca sao armazenadas em texto: apenas o hash bcrypt. Os tokens sao
assinados com HMAC-SHA256 e carregam expiracao, de modo que nao dependem de
estado no servidor.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import config
from .database import obter_sessao
from .models import Usuario, agora

log = logging.getLogger(__name__)

esquema_bearer = HTTPBearer(auto_error=False)

PERFIL_DEMANDANTE = "demandante"
PERFIL_GESTOR = "gestor"
PERFIL_SISTEMA = "sistema"


# ------------------------------------------------------------------ senhas
def gerar_hash_senha(senha: str) -> str:
    if len(senha) < 8:
        raise ValueError("a senha deve ter ao menos 8 caracteres")
    return bcrypt.hashpw(
        senha.encode("utf-8"), bcrypt.gensalt(rounds=config.BCRYPT_RODADAS)
    ).decode("utf-8")


def conferir_senha(senha: str, hash_armazenado: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), hash_armazenado.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------ tokens
def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).decode("ascii").rstrip("=")


def _deb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def gerar_token(usuario: Usuario) -> str:
    """Token assinado com HMAC-SHA256, com expiracao embutida."""
    expira = agora() + timedelta(minutes=config.TOKEN_EXPIRA_MINUTOS)
    corpo = {
        "sub": usuario.id_usuario,
        "email": usuario.email,
        "perfil": usuario.perfil,
        "exp": expira.isoformat(timespec="seconds"),
        "jti": secrets.token_hex(8),
    }
    payload = _b64(json.dumps(corpo, sort_keys=True).encode("utf-8"))
    assinatura = hmac.new(
        config.SECRET_KEY.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload}.{_b64(assinatura)}"


def validar_token(token: str) -> dict[str, Any]:
    """Confere assinatura e expiracao, devolvendo o conteudo do token."""
    try:
        payload, assinatura = token.split(".", 1)
    except ValueError as erro:
        raise ValueError("token malformado") from erro

    esperada = hmac.new(
        config.SECRET_KEY.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        recebida = _deb64(assinatura)
    except ValueError as erro:
        raise ValueError("token malformado") from erro
    if not hmac.compare_digest(recebida, esperada):
        raise ValueError("assinatura invalida")

    try:
        corpo = json.loads(_deb64(payload))
        expira = datetime.fromisoformat(corpo["exp"])
        int(corpo["sub"])
    except (KeyError, TypeError, ValueError) as erro:
        raise ValueError("token malformado") from erro

    if expira < agora():
        raise ValueError("token expirado")
    return corpo


# ------------------------------------------------------------ dependencias
def usuario_atual(
    credenciais: HTTPAuthorizationCredentials | None = Depends(esquema_bearer),
    sessao: Session = Depends(obter_sessao),
) -> Usuario:
    """Usuario autenticado da requisicao."""
    if credenciais is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        corpo = validar_token(credenciais.credentials)
    except ValueError as erro:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Sessão inválida: {erro}. Entre novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from erro

    usuario = sessao.get(Usuario, corpo["sub"])
    if usuario is None or not usuario.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário inativo ou inexistente."
        )
    return usuario


def exigir_perfil(*perfis: str):
    """Dependencia que restringe a rota aos perfis informados."""

    def verificador(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
        if usuario.perfil not in perfis:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Ação restrita aos perfis: {', '.join(perfis)}.",
            )
        return usuario

    return verificador


__all__ = [
    "gerar_hash_senha",
    "conferir_senha",
    "gerar_token",
    "validar_token",
    "usuario_atual",
    "exigir_perfil",
    "PERFIL_DEMANDANTE",
    "PERFIL_GESTOR",
    "PERFIL_SISTEMA",
]
