"""Conexão com o banco e aplicação do esquema.

Suporta SQLite (padrão de desenvolvimento) e MySQL 8. A troca é feita apenas pela
variável DB_DIALETO: o modelo de dados e as regras de negócio são os mesmos, e cada
dialeto tem seu arquivo de DDL com os gatilhos equivalentes (RN01 e RN05).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .config import config

log = logging.getLogger(__name__)

# Erros do MySQL que significam "objeto ja existe": o MySQL nao tem
# CREATE INDEX IF NOT EXISTS, e o esquema e reaplicado a cada subida.
#   1061 = nome de indice duplicado; 1359 = gatilho ja existe.
MYSQL_JA_EXISTE = {1061, 1359}


def _criar_engine() -> Engine:
    if config.DB_DIALETO == "sqlite":
        eng = create_engine(
            config.url_banco,
            echo=config.DEBUG,
            future=True,
            # timeout: espera o bloqueio de escrita em vez de falhar na hora
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")   # integridade referencial
            cur.execute("PRAGMA journal_mode=WAL")  # leitura concorrente
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        return eng

    return create_engine(
        config.url_banco,
        echo=config.DEBUG,
        future=True,
        pool_pre_ping=True,   # reconecta após timeout do MySQL
        pool_recycle=3600,
    )


engine: Engine = _criar_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def obter_sessao() -> Iterator[Session]:
    """Dependência do FastAPI: uma sessão por requisição."""
    sessao = SessionLocal()
    try:
        yield sessao
    finally:
        sessao.close()


@contextmanager
def sessao_escopo() -> Iterator[Session]:
    """Sessão transacional para uso fora do FastAPI (scripts, testes)."""
    sessao = SessionLocal()
    try:
        yield sessao
        sessao.commit()
    except Exception:
        sessao.rollback()
        raise
    finally:
        sessao.close()


def _comandos(ddl: str) -> list[str]:
    """Divide o DDL em comandos, preservando a ordem e os corpos de gatilho.

    Gatilhos contem ';' internos; por isso o arquivo marca esses blocos com
    '-- >>>' e '-- <<<'. A ordem do arquivo e preservada: criar um gatilho antes
    da tabela a que ele se refere falha.
    """
    comandos: list[str] = []
    soltas: list[str] = []
    bloco: list[str] = []
    dentro_de_bloco = False

    def descarregar_soltas() -> None:
        """Converte as linhas acumuladas fora de bloco em comandos individuais."""
        if not soltas:
            return
        texto = re.sub(r"--[^\n]*", "", "\n".join(soltas))
        for comando in texto.split(";"):
            if comando.strip():
                comandos.append(comando.strip())
        soltas.clear()

    for linha in ddl.splitlines():
        marca = linha.strip()
        if marca.startswith("-- >>>"):
            descarregar_soltas()          # tudo que veio antes vai primeiro
            dentro_de_bloco = True
            bloco = []
            continue
        if marca.startswith("-- <<<"):
            if not dentro_de_bloco:
                raise ValueError("DDL malformado: '-- <<<' sem '-- >>>' correspondente")
            dentro_de_bloco = False
            if (corpo := "\n".join(bloco).strip()):
                comandos.append(corpo)
            bloco = []
            continue
        (bloco if dentro_de_bloco else soltas).append(linha)

    if dentro_de_bloco:
        raise ValueError("DDL malformado: bloco '-- >>>' sem '-- <<<' correspondente")

    descarregar_soltas()
    return comandos


def _ja_existe(erro: DBAPIError) -> bool:
    """Indica se o erro do banco e apenas 'objeto ja existe' (MySQL)."""
    argumentos = getattr(erro.orig, "args", ())
    return bool(argumentos) and argumentos[0] in MYSQL_JA_EXISTE


def aplicar_esquema() -> None:
    """Cria tabelas, índices e gatilhos, se ainda não existirem (idempotente)."""
    arquivo = config.arquivo_ddl
    if not arquivo.exists():
        raise FileNotFoundError(f"DDL não encontrado: {arquivo}")

    ddl = arquivo.read_text(encoding="utf-8")
    for comando in _comandos(ddl):
        # Uma transacao por comando: no MySQL, DDL faz commit implicito, e um
        # "ja existe" nao pode abortar os comandos seguintes.
        try:
            with engine.begin() as conexao:
                conexao.execute(text(comando))
        except DBAPIError as erro:
            if config.DB_DIALETO == "mysql" and _ja_existe(erro):
                continue
            raise
    log.info("Esquema aplicado (%s): %s", config.DB_DIALETO, arquivo.name)


def saude() -> bool:
    """Verifica se o banco responde."""
    try:
        with engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
        return True
    except Exception as erro:  # noqa: BLE001
        log.error("Banco indisponível: %s", erro)
        return False


__all__ = [
    "engine",
    "SessionLocal",
    "obter_sessao",
    "sessao_escopo",
    "aplicar_esquema",
    "saude",
]
