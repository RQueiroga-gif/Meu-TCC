"""Rotas de autenticacao (UC01, RF01)."""
from __future__ import annotations

import logging
import threading
import time

import bcrypt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import config
from ..core import auditoria
from ..database import obter_sessao
from ..models import Usuario
from ..schemas import LoginEntrada, LoginSaida, UsuarioSaida
from ..security import conferir_senha, gerar_token, usuario_atual

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Autenticação"])


class LimitadorTentativas:
    """Bloqueio temporario apos falhas seguidas de login (forca bruta).

    Mantido em memoria: suficiente para um processo da API. Com varias
    instancias atras de um balanceador, o estado precisaria ir para o banco.
    """

    def __init__(self) -> None:
        self._falhas: dict[str, list[float]] = {}
        self._trava = threading.Lock()

    def _janela(self) -> float:
        return config.LOGIN_BLOQUEIO_MINUTOS * 60

    def bloqueado(self, chave: str) -> bool:
        agora = time.monotonic()
        with self._trava:
            recentes = [t for t in self._falhas.get(chave, []) if agora - t < self._janela()]
            self._falhas[chave] = recentes
            return len(recentes) >= config.LOGIN_MAX_TENTATIVAS

    def falhou(self, chave: str) -> None:
        with self._trava:
            self._falhas.setdefault(chave, []).append(time.monotonic())

    def sucesso(self, chave: str) -> None:
        with self._trava:
            self._falhas.pop(chave, None)


limitador = LimitadorTentativas()

# Hash de uma senha qualquer: conferido quando o usuario nao existe, para que a
# resposta leve o mesmo tempo nos dois casos e nao revele quais e-mails estao
# cadastrados pela diferenca de tempo (o bcrypt domina o custo da requisicao).
_HASH_FICTICIO = bcrypt.hashpw(b"usuario-inexistente", bcrypt.gensalt(rounds=config.BCRYPT_RODADAS)).decode()


@router.post("/login", response_model=LoginSaida, summary="Autenticar usuário")
def login(dados: LoginEntrada, sessao: Session = Depends(obter_sessao)) -> LoginSaida:
    """Confere as credenciais e devolve o token de sessão."""
    email = dados.email.lower()
    if limitador.bloqueado(email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Muitas tentativas sem sucesso. Aguarde "
                f"{config.LOGIN_BLOQUEIO_MINUTOS} minutos e tente novamente."
            ),
        )

    usuario = sessao.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none()

    # Mensagem unica para usuario inexistente e senha errada: nao revela quais
    # e-mails estao cadastrados.
    senha_ok = conferir_senha(dados.senha, usuario.senha_hash if usuario else _HASH_FICTICIO)
    if usuario is None or not usuario.ativo or not senha_ok:
        limitador.falhou(email)
        log.warning("Tentativa de acesso recusada para %s", email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha inválidos.",
        )

    limitador.sucesso(email)
    auditoria.registrar(sessao, id_usuario=usuario.id_usuario, acao="ACESSO")
    sessao.commit()

    return LoginSaida(
        token=gerar_token(usuario),
        perfil=usuario.perfil,
        nome=usuario.nome,
        unidade=usuario.unidade,
        expira_em_minutos=config.TOKEN_EXPIRA_MINUTOS,
    )


@router.get("/eu", response_model=UsuarioSaida, summary="Dados do usuário autenticado")
def eu(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
    return usuario
