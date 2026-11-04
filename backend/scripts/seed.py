"""Popula o banco para desenvolvimento e demonstracao.

Cria os usuarios dos dois perfis e indexa as normas. Usa o texto oficial integral
de ingestao/oficial (Lei nº 14.133/2021, Decreto nº 10.947/2022 e LGPD, com hash
registrado em MANIFESTO.yaml) e, na falta dele, as amostras de ingestao/amostra.
Para atualizar o texto oficial a partir do Planalto: python ../ingestao/baixar.py

Uso:
    python -m scripts.seed
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import aplicar_esquema, sessao_escopo  # noqa: E402
from app.models import Usuario  # noqa: E402
from app.security import gerar_hash_senha  # noqa: E402
from app.services.ingestao import ingerir_arquivo, reindexar_base  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger("seed")

INGESTAO = Path(__file__).resolve().parent.parent.parent / "ingestao"
OFICIAL = INGESTAO / "oficial"
AMOSTRAS = INGESTAO / "amostra"

USUARIOS = [
    {
        "nome": "Ronan Rocha Queiroga",
        "email": "demandante@orgao.gov.br",
        "perfil": "demandante",
        "senha": "demandante123",
        "unidade": "Coordenadoria de Protocolo",
    },
    {
        "nome": "Gestora da Base Normativa",
        "email": "gestor@orgao.gov.br",
        "perfil": "gestor",
        "senha": "gestor123",
        "unidade": "Departamento de Licitações",
    },
    {
        "nome": "Sistema",
        "email": "sistema@orgao.gov.br",
        "perfil": "sistema",
        "senha": "sistema-sem-acesso-interativo",
        "unidade": None,
    },
]

NORMAS = [
    {
        "oficial": "L14133.htm",
        "arquivo": "l14133_amostra.html",
        "sigla": "L14133",
        "tipo": "Lei",
        "numero": "14.133",
        "ano": 2021,
        "ementa": "Lei de Licitações e Contratos Administrativos.",
        "url_fonte": "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm",
    },
    {
        "oficial": "D10947.htm",
        "arquivo": "d10947_amostra.html",
        "sigla": "D10947",
        "tipo": "Decreto",
        "numero": "10.947",
        "ano": 2022,
        "ementa": "Regulamenta o plano de contratações anual e o documento de formalização da demanda.",
        "url_fonte": "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2022/decreto/d10947.htm",
    },
    {
        "oficial": "L13709.htm",
        "arquivo": None,                       # sem amostra: so com o texto oficial
        "sigla": "L13709",
        "tipo": "Lei",
        "numero": "13.709",
        "ano": 2018,
        "ementa": "Lei Geral de Proteção de Dados Pessoais (LGPD).",
        "url_fonte": "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm",
    },
]


def main() -> int:
    analisador = argparse.ArgumentParser(description="Povoamento de desenvolvimento")
    analisador.add_argument(
        "--reindexar",
        action="store_true",
        help="reconstroi o indice vetorial a partir do banco (apos trocar o gerador "
        "de embeddings, por exemplo ao instalar o sentence-transformers)",
    )
    analisador.add_argument(
        "--forcar",
        action="store_true",
        help="ressegmenta e reindexa as normas mesmo sem alteracao na fonte",
    )
    argumentos = analisador.parse_args()

    aplicar_esquema()

    if argumentos.reindexar:
        with sessao_escopo() as sessao:
            total = reindexar_base(sessao)
        log.info("Índice reconstruído: %d trechos.", total)
        return 0

    with sessao_escopo() as sessao:
        for dados in USUARIOS:
            existente = sessao.execute(
                select(Usuario).where(Usuario.email == dados["email"])
            ).scalar_one_or_none()
            if existente:
                log.info("usuário já existe: %s", dados["email"])
                continue
            sessao.add(
                Usuario(
                    nome=dados["nome"],
                    email=dados["email"],
                    perfil=dados["perfil"],
                    senha_hash=gerar_hash_senha(dados["senha"]),
                    unidade=dados["unidade"],
                    ativo=dados["perfil"] != "sistema",
                )
            )
            log.info("usuário criado: %s (%s)", dados["email"], dados["perfil"])

    with sessao_escopo() as sessao:
        for dados_norma in NORMAS:
            norma = dict(dados_norma)
            oficial, amostra = norma.pop("oficial"), norma.pop("arquivo")
            caminho = OFICIAL / oficial
            if not caminho.exists():
                if amostra is None:
                    log.info("%s: texto oficial ausente; norma ignorada", norma["sigla"])
                    continue
                caminho = AMOSTRAS / amostra
                log.warning("%s: usando a AMOSTRA (texto oficial ausente)", norma["sigla"])
            if not caminho.exists():
                log.warning("arquivo ausente: %s", caminho.name)
                continue
            resultado = ingerir_arquivo(sessao, caminho, forcar=argumentos.forcar, **norma)
            log.info("%s", resultado)

    log.info("Seed concluído.")
    log.info("Credenciais de desenvolvimento:")
    for dados in USUARIOS[:2]:
        log.info("  %-28s senha: %s", dados["email"], dados["senha"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
