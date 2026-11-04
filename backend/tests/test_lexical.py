"""Indice lexical (BM25) e modos de recuperacao (versao 0.3)."""
from __future__ import annotations

import pytest

from app.config import config
from app.core.embeddings import EmbedderHash, redefinir_embedder
from app.core.indice import IndiceNumpy
from app.core.lexical import IndiceComposto, IndiceLexical, Tokenizador, modo_efetivo

ITENS = [
    {"referencia_vetorial": "L|art. 75, VIII", "referencia": "Lei, art. 75, VIII",
     "texto_indexado": "dispensável a licitação nos casos de emergência ou de calamidade pública"},
    {"referencia_vetorial": "L|art. 6º, X", "referencia": "Lei, art. 6º, X",
     "texto_indexado": "compra: aquisição remunerada de bens para fornecimento de uma só vez"},
    {"referencia_vetorial": "L|art. 51, caput", "referencia": "Lei, art. 51, caput",
     "texto_indexado": "a locação de imóveis deverá ser precedida de licitação e avaliação prévia"},
]


def test_tokenizador_remove_acento_vazias_e_reduz_ao_radical():
    tok = Tokenizador()
    assert tok("Aquisição de computadores") == tok("aquisicao dos computador")


def test_bm25_ordena_pelo_termo_raro(tmp_path):
    indice = IndiceLexical(tmp_path / "lex.json.gz")
    indice.indexar(ITENS)
    achados = indice.buscar("contratação emergencial por calamidade", 2)
    assert achados[0].referencia_vetorial == "L|art. 75, VIII"
    assert 0 < achados[0].similaridade < 1


def test_bm25_persiste_e_respeita_vigencia(tmp_path):
    caminho = tmp_path / "lex.json.gz"
    IndiceLexical(caminho).indexar(ITENS)
    indice = IndiceLexical(caminho)                 # recarrega do disco
    assert indice.total() == 3
    indice.atualizar_vigencia(["L|art. 51, caput"], False)
    assert not indice.buscar("locação de imóveis", 3)
    assert indice.buscar("locação de imóveis", 3, apenas_vigentes=False)


@pytest.fixture
def composto(tmp_path):
    redefinir_embedder(EmbedderHash())
    c = IndiceComposto(IndiceNumpy(tmp_path / "v.npz"), IndiceLexical(tmp_path / "l.json.gz"))
    c.indexar(ITENS)
    yield c
    redefinir_embedder(EmbedderHash())


@pytest.mark.parametrize("modo", ["denso", "lexical", "hibrido"])
def test_modos_devolvem_resultados(composto, monkeypatch, modo):  # noqa: ANN001
    monkeypatch.setattr(config, "RECUPERACAO_MODO", modo)
    assert modo_efetivo() == modo
    achados = composto.buscar("locação de imóvel para a unidade", 2)
    assert achados and len(achados) <= 2
    assert 0 <= composto.similaridade_maxima("locação de imóvel") <= 1


def test_automatico_sem_bge_usa_lexical(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(config, "RECUPERACAO_MODO", "automatico")
    redefinir_embedder(EmbedderHash())
    assert modo_efetivo() == "lexical"


def test_automatico_com_bge_usa_hibrido(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(config, "RECUPERACAO_MODO", "automatico")
    falso_bge = EmbedderHash()
    falso_bge.identificador = "bge:BAAI/bge-m3"
    redefinir_embedder(falso_bge)
    assert modo_efetivo() == "hibrido"
    redefinir_embedder(EmbedderHash())


def test_lexical_reconstruido_do_vetorial(tmp_path):
    redefinir_embedder(EmbedderHash())
    denso = IndiceNumpy(tmp_path / "v.npz")
    denso.indexar(ITENS)
    novo = IndiceLexical(tmp_path / "l.json.gz")
    novo.indexar(denso.itens())
    assert novo.total() == 3


def test_portao_lexical_usa_cosseno(composto, monkeypatch):  # noqa: ANN001
    """No modo lexical o portao olha o cosseno, nao a pontuacao BM25."""
    from app.core import indice as modulo_indice
    from app.core.recuperacao import recuperar

    monkeypatch.setattr(config, "RECUPERACAO_MODO", "lexical")
    monkeypatch.setattr(modulo_indice, "_indice", composto)
    aceito = recuperar("emergência e calamidade pública", k=2, limiar=0.0)
    barrado = recuperar("emergência e calamidade pública", k=2, limiar=0.999)
    assert aceito.suficiente and aceito.trechos
    assert not barrado.suficiente and not barrado.trechos
    monkeypatch.setattr(modulo_indice, "_indice", None)


def test_hibrido_vetoriza_a_consulta_uma_vez(tmp_path, monkeypatch):  # noqa: ANN001
    """Lista do RRF e portao reaproveitam o mesmo vetor da consulta."""
    contador = EmbedderHash()
    original = contador.codificar
    chamadas: list[int] = []

    def contar(textos):  # noqa: ANN001
        chamadas.append(len(textos))
        return original(textos)

    contador.codificar = contar
    redefinir_embedder(contador)
    denso = IndiceNumpy(tmp_path / "v.npz")
    lexical = IndiceLexical(tmp_path / "l.json.gz")
    composto = IndiceComposto(denso, lexical)
    composto.indexar([
        {"referencia_vetorial": f"L|art. {i}", "referencia": f"art. {i}",
         "texto_indexado": f"dispensa de licitação por valor {i}", "vigente": True}
        for i in range(5)
    ])
    monkeypatch.setattr(config, "RECUPERACAO_MODO", "hibrido")
    chamadas.clear()
    composto.buscar("dispensa por valor", 3)
    composto.similaridade_maxima("dispensa por valor")
    assert chamadas == [1]
    redefinir_embedder(EmbedderHash())
