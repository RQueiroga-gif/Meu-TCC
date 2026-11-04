"""Roteiro do E9: metricas de citacao, amostra, retomada e cache de embeddings.

Nenhum teste chama provedor real: a geracao e substituida por uma funcao falsa.
"""
from __future__ import annotations

import contextlib
import json

import numpy as np

from app.core.embeddings import EmbedderHash
from avaliacao import experimentos as E
from avaliacao.cache_embeddings import EmbedderComCache


def test_citacoes_classifica_inexistente_pertinente_e_revogada():
    existentes = {("L14133", "75"), ("L14133", "6")}
    relevantes = {("L14133", "75")}
    texto = ("Fundamenta-se no art. 75 da Lei nº 14.133/2021, no art. 250 da Lei nº 14.133/2021 "
             "e no art. 24 da Lei nº 8.666/1993.")
    r = E._citacoes_e9(texto, existentes, relevantes)
    assert r["citacoes"] == 3
    assert r["citacoes_pertinentes"] == 1         # art. 75
    assert r["citacoes_inexistentes"] == 1        # art. 250 nao existe
    assert r["citacoes_norma_revogada"] == 1      # Lei 8.666
    assert r["menciona_norma_revogada"] is True


def test_amostra_espacada_e_por_ids():
    todas = E.amostra_demandas()
    seis = E.amostra_demandas(6)
    assert len(seis) == 6 and seis[0]["id"] == todas[0]["id"]
    assert [d["id"] for d in seis] == [d["id"] for d in E.amostra_demandas(6)]   # deterministica
    assert [d["id"] for d in E.amostra_demandas(ids=["d02", "D01"])] == ["D01", "D02"]


def _fakes(monkeypatch, respostas):  # noqa: ANN001
    """Substitui a API e a geracao: cada chamada consome o proximo status da fila."""
    chamadas = []

    def linha(_cli, _gestor, _prov, d, condicao, _existentes):  # noqa: ANN001
        status = respostas.pop(0)
        chamadas.append((d["id"], condicao))
        ln = {"id": d["id"], "condicao": condicao, "status": status, "latencia_s": 0.1}
        if status == 200:
            ln.update(citacoes=1, citacoes_pertinentes=1, citacoes_inexistentes=0,
                      citacoes_norma_revogada=0, menciona_norma_revogada=False,
                      valores_presentes=1.0, ancoragem=1.0, inconsistencia=False,
                      normas_fora=0, extracao_correta=1.0)
        elif status == 503:
            ln["erro"] = "cota diaria do Gemini esgotada para o modelo"
        else:
            ln["erro"] = "provedor respondeu 500"
        return ln

    monkeypatch.setattr(E, "_linha_e9", linha)
    monkeypatch.setattr(E, "_cliente", contextlib.nullcontext)
    monkeypatch.setattr(E, "_entrar", lambda *_a: {})
    return chamadas


def test_e9_para_na_cota_diaria_e_retoma(monkeypatch, tmp_path):
    demandas = E.amostra_demandas(ids=["D01", "D02"])
    ck = tmp_path / "e9.jsonl"
    chamadas = _fakes(monkeypatch, [200, 200, 503])
    r = E.e9_llm("gemini", condicoes=["completo", "isolada"], demandas=demandas, checkpoint=ck)
    assert r["interrompido"] and "cota diaria" in r["interrompido"]
    assert not r["concluido"] and r["pendentes"] == 2
    assert len(chamadas) == 3                      # parou sem tentar a 4a geracao

    chamadas = _fakes(monkeypatch, [200, 200])
    r = E.e9_llm("gemini", condicoes=["completo", "isolada"], demandas=demandas, checkpoint=ck)
    assert chamadas == [("D02", "completo"), ("D02", "isolada")]   # so o que faltava
    assert r["concluido"] and r["resumo"][0]["n"] == 2
    assert len(ck.read_text(encoding="utf-8").splitlines()) == 5


def test_e9_para_apos_falhas_seguidas(monkeypatch, tmp_path):
    demandas = E.amostra_demandas(ids=["D01", "D02"])
    chamadas = _fakes(monkeypatch, [500, 500, 500])
    r = E.e9_llm("anthropic", condicoes=["completo", "isolada"], demandas=demandas,
                 checkpoint=tmp_path / "e9.jsonl", max_falhas_seguidas=3)
    assert len(chamadas) == 3 and "500" in r["interrompido"]
    assert r["resumo"] == []


def test_cache_de_embeddings_reaproveita_vetores(tmp_path):
    base = EmbedderHash()
    cache = EmbedderComCache(base, pasta=tmp_path)
    a = cache.codificar(["licitação", "dispensa"])
    assert cache.calculados == 2
    novo = EmbedderComCache(base, pasta=tmp_path)          # outra execucao
    b = novo.codificar(["dispensa", "licitação"])
    assert novo.calculados == 0 and novo.acertos == 2
    assert np.allclose(a[::-1], b)
    assert novo.identificador == base.identificador         # indice compativel
    assert json.dumps(novo.limiar)
