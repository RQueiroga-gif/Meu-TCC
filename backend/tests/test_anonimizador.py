"""Minimizacao de dados pessoais, versao 0.3 (RN06).

Os casos reproduzem as falhas da versao 0.2.0 encontradas no conjunto de
desenvolvimento (avaliacao/dados/dados_pessoais.yaml).
"""
from __future__ import annotations

import importlib.util

import pytest

from app.config import config
from app.core import anonimizador
from app.core.anonimizador import minimizar


@pytest.mark.parametrize(
    ("texto", "rotulo"),
    [
        ("CNPJ alfanumérico 12.ABC.345/01DE-35.", "CNPJ"),
        ("Empresa 11222333000181 (dígitos válidos).", "CNPJ"),
        ("Servidor 52998224725 (dígitos válidos).", "CPF"),
        ("Telefone (45)998765432 da chefia.", "TELEFONE"),
        ("WhatsApp +55 45 99876-5432.", "TELEFONE"),
        ("Contato pelo celular 45998765432.", "TELEFONE"),
        ("CEP 85.851-000.", "CEP"),
        ("Portador do RG 12.345.678-9.", "RG"),
    ],
)
def test_formatos_que_a_versao_anterior_nao_tratava(texto: str, rotulo: str):
    r = minimizar(texto, nomes=False)
    assert f"[{rotulo}]" in r.texto, r.texto
    assert r.ocorrencias.get(rotulo) == 1


@pytest.mark.parametrize(
    "texto",
    [
        "Plano de contratações anual, código PCA 2027-0458.",
        "Processo SEI nº 23000.012345/2026-11.",
        "Nota de empenho 2026NE000123, das 08:00 às 17:00.",
        "Valor total de R$ 159.996,00 em 30/06/2027.",
        "Emenda parlamentar 20264123000.",      # 11 digitos, CPF invalido
    ],
)
def test_codigos_administrativos_nao_viram_dado_pessoal(texto: str):
    assert minimizar(texto, nomes=False).texto == texto


def test_numero_sem_digito_valido_e_mascarado_se_rotulado():
    r = minimizar("CPF 12345678900 informado pelo servidor.", nomes=False)
    assert r.texto == "CPF [CPF] informado pelo servidor."


@pytest.fixture
def com_ner(monkeypatch):  # noqa: ANN001
    if importlib.util.find_spec("spacy") is None:
        pytest.skip("spaCy não instalado")
    monkeypatch.setattr(config, "ANONIMIZADOR_NER", True)
    anonimizador._modelo_ner.cache_clear()
    if not anonimizador.ner_disponivel():
        pytest.skip("modelo pt_core_news_md não instalado")
    yield
    anonimizador._modelo_ner.cache_clear()


def test_nome_de_pessoa_e_mascarado(com_ner):  # noqa: ANN001, ARG001
    r = minimizar("A demanda foi apresentada pelo servidor João Carlos da Silva, do protocolo.")
    assert r.texto == "A demanda foi apresentada pelo servidor [NOME], do protocolo."


def test_nome_em_denominacao_de_instituicao_e_preservado(com_ner):  # noqa: ANN001, ARG001
    texto = "Reforma da Escola Municipal Tancredo Neves."
    assert minimizar(texto).texto == texto


def test_marcador_nao_e_tomado_por_nome(com_ner):  # noqa: ANN001, ARG001
    r = minimizar("Fornecedor CNPJ 12.345.678/0001-90 habilitado.")
    assert r.texto == "Fornecedor CNPJ [CNPJ] habilitado."
