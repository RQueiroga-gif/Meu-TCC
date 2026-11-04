"""Auditoria encadeada por hash (RN05, RNF07).

Cada registro carrega o hash do registro anterior, de modo que a adulteracao de
qualquer linha quebra a cadeia e e detectada por reprocessamento. O banco impede
UPDATE e DELETE na tabela por gatilho; esta camada garante o encadeamento.

A funcao de hash roda na aplicacao (e nao no banco) para que o comportamento seja
identico em SQLite e MySQL. A verificacao (`verificar_cadeia`) recalcula a cadeia
inteira e aponta o primeiro registro divergente.

Concorrencia: a leitura do ultimo registro usa SELECT ... FOR UPDATE (efetivo no
MySQL), e o indice unico em hash_anterior faz o banco recusar uma bifurcacao se
duas transacoes lerem o mesmo "ultimo" — a cadeia nunca se corrompe em silencio.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RegistroAuditoria, agora

log = logging.getLogger(__name__)

SEM_ANTERIOR = ""   # valor usado no encadeamento do primeiro registro


def _serializar(detalhe: dict[str, Any] | None) -> str:
    """Serializa o detalhe de forma estavel (chaves ordenadas)."""
    if not detalhe:
        return ""
    return json.dumps(detalhe, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def calcular_hash(
    *,
    hash_anterior: str | None,
    id_usuario: int,
    id_dfd: int | None,
    acao: str,
    detalhe: dict[str, Any] | None,
    data_hora: datetime,
) -> str:
    """Calcula o hash SHA-256 de um registro, encadeado ao anterior."""
    material = "|".join(
        (
            hash_anterior or SEM_ANTERIOR,
            str(id_usuario),
            str(id_dfd) if id_dfd is not None else "",
            acao,
            _serializar(detalhe),
            data_hora.isoformat(timespec="seconds"),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def registrar(
    sessao: Session,
    *,
    id_usuario: int,
    acao: str,
    id_dfd: int | None = None,
    detalhe: dict[str, Any] | None = None,
) -> RegistroAuditoria:
    """Acrescenta um evento a auditoria.

    O registro e adicionado a sessao, mas nao committado: a auditoria participa da
    mesma transacao da acao auditada, de modo que ou ambas persistem, ou nenhuma.
    Chame esta funcao o mais perto possivel do commit, para segurar o bloqueio
    do ultimo registro pelo menor tempo.
    """
    # Normaliza o detalhe pelo mesmo caminho do banco (JSON), para que o hash
    # calculado agora seja identico ao recalculado na verificacao.
    detalhe = json.loads(json.dumps(detalhe, ensure_ascii=False)) if detalhe else None

    ultimo = sessao.execute(
        select(RegistroAuditoria)
        .order_by(RegistroAuditoria.id_registro.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()

    momento = agora()
    anterior = ultimo.hash_registro if ultimo else None
    registro = RegistroAuditoria(
        id_usuario=id_usuario,
        id_dfd=id_dfd,
        acao=acao.upper(),
        detalhe=detalhe,
        data_hora=momento,
        hash_anterior=anterior,
        hash_registro=calcular_hash(
            hash_anterior=anterior,
            id_usuario=id_usuario,
            id_dfd=id_dfd,
            acao=acao.upper(),
            detalhe=detalhe,
            data_hora=momento,
        ),
    )
    sessao.add(registro)
    sessao.flush()
    log.debug("Auditoria: %s (dfd=%s, usuario=%s)", acao, id_dfd, id_usuario)
    return registro


def verificar_cadeia(sessao: Session) -> dict[str, Any]:
    """Recalcula a cadeia inteira e reporta a integridade.

    Returns:
        {"integra": bool, "total": int, "primeiro_divergente": int | None}
    """
    registros = (
        sessao.execute(
            select(RegistroAuditoria)
            .order_by(RegistroAuditoria.id_registro)
            # Le o estado real do banco, ignorando objetos ja carregados na
            # sessao: a verificacao de integridade nao pode confiar em cache.
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )

    anterior: str | None = None
    divergente: int | None = None
    for registro in registros:
        esperado = calcular_hash(
            hash_anterior=anterior,
            id_usuario=registro.id_usuario,
            id_dfd=registro.id_dfd,
            acao=registro.acao,
            detalhe=registro.detalhe,
            data_hora=registro.data_hora,
        )
        if esperado != registro.hash_registro or (registro.hash_anterior or None) != anterior:
            divergente = registro.id_registro
            break
        anterior = registro.hash_registro

    return {
        "integra": divergente is None,
        "total": len(registros),
        "primeiro_divergente": divergente,
    }


__all__ = ["registrar", "verificar_cadeia", "calcular_hash"]
