"""Ambiente de teste: banco temporario, indice isolado e cliente HTTP.

Cada sessao de teste usa um SQLite proprio em diretorio temporario, de modo que
nenhum teste toca a base de desenvolvimento.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


def _configurar_ambiente(pasta: Path) -> None:
    os.environ.update(
        {
            "DB_DIALETO": "sqlite",
            "DB_ARQUIVO": str(pasta / "teste.sqlite3"),
            "INDICE_ARQUIVO": str(pasta / "indice.npz"),
            "INDICE_BACKEND": "numpy",
            "INDICE_LEXICAL_ARQUIVO": str(pasta / "indice_lexical.json.gz"),
            "RECUPERACAO_MODO": "denso",          # cada modo tem testes proprios
            "LLM_PROVEDOR": "echo",
            "SECRET_KEY": "chave-de-teste-apenas",
            "DEBUG": "false",
            "RECUPERACAO_LIMIAR_HASH": "0.10",   # gerador de embeddings de teste
            "EMBEDDING_MODELO": "hash",
            "PASTA_SAIDA": str(pasta / "saida"),
            "BCRYPT_RODADAS": "4",               # hash rapido: so nos testes
            "ANONIMIZADOR_NER": "false",         # resultado identico com ou sem spaCy
        }
    )


# Aplicado na importacao do conftest, antes da coleta: um modulo de teste que
# importe a aplicacao no topo (e, com ela, a configuracao) ja ve o banco
# descartavel, e nunca a base de desenvolvimento.
_configurar_ambiente(Path(tempfile.mkdtemp(prefix="dfd-teste-")))


@pytest.fixture(scope="session", autouse=True)
def ambiente(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Aponta a configuracao para um banco e um indice descartaveis."""
    _configurar_ambiente(tmp_path_factory.mktemp("dfd-teste"))
    from app.config import obter_config

    obter_config.cache_clear()


@pytest.fixture(scope="session")
def app_pronta(ambiente):  # noqa: ANN001, ARG001
    """Aplicacao com esquema aplicado e base normativa de amostra indexada."""
    from app.core.embeddings import EmbedderHash, redefinir_embedder
    from app.database import aplicar_esquema, sessao_escopo
    from app.main import app
    from app.models import Usuario
    from app.security import gerar_hash_senha
    from app.services.ingestao import ingerir_arquivo

    redefinir_embedder(EmbedderHash())   # deterministico, sem download de modelo
    aplicar_esquema()

    amostras = RAIZ.parent / "ingestao" / "amostra"
    with sessao_escopo() as sessao:
        sessao.add_all(
            [
                Usuario(
                    nome="Agente Demandante",
                    email="demandante@orgao.gov.br",
                    perfil="demandante",
                    senha_hash=gerar_hash_senha("demandante123"),
                    unidade="Coordenadoria de Protocolo",
                ),
                Usuario(
                    nome="Outro Demandante",
                    email="outro@orgao.gov.br",
                    perfil="demandante",
                    senha_hash=gerar_hash_senha("outro12345"),
                    unidade="Almoxarifado",
                ),
                Usuario(
                    nome="Gestor da Base",
                    email="gestor@orgao.gov.br",
                    perfil="gestor",
                    senha_hash=gerar_hash_senha("gestor123"),
                    unidade="Departamento de Licitações",
                ),
            ]
        )

    with sessao_escopo() as sessao:
        ingerir_arquivo(
            sessao,
            amostras / "l14133_amostra.html",
            sigla="L14133",
            tipo="Lei",
            numero="14.133",
            ano=2021,
            ementa="Lei de Licitações e Contratos Administrativos.",
        )
        ingerir_arquivo(
            sessao,
            amostras / "d10947_amostra.html",
            sigla="D10947",
            tipo="Decreto",
            numero="10.947",
            ano=2022,
            ementa="Regulamenta o plano de contratações anual.",
        )
    return app


@pytest.fixture
def cliente(app_pronta):  # noqa: ANN001
    from fastapi.testclient import TestClient

    with TestClient(app_pronta) as c:
        yield c


@pytest.fixture
def token(cliente) -> str:  # noqa: ANN001
    resposta = cliente.post(
        "/auth/login", json={"email": "demandante@orgao.gov.br", "senha": "demandante123"}
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["token"]


@pytest.fixture
def cabecalho(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _entrar(cliente, email: str, senha: str) -> dict[str, str]:  # noqa: ANN001
    resposta = cliente.post("/auth/login", json={"email": email, "senha": senha})
    assert resposta.status_code == 200, resposta.text
    return {"Authorization": f"Bearer {resposta.json()['token']}"}


@pytest.fixture
def cabecalho_gestor(cliente) -> dict[str, str]:  # noqa: ANN001
    return _entrar(cliente, "gestor@orgao.gov.br", "gestor123")


@pytest.fixture
def cabecalho_outro(cliente) -> dict[str, str]:  # noqa: ANN001
    return _entrar(cliente, "outro@orgao.gov.br", "outro12345")


@pytest.fixture
def sessao():
    from app.database import sessao_escopo

    with sessao_escopo() as s:
        yield s
