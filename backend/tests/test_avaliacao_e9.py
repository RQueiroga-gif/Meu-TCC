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
    r = E.e9_llm("groq", condicoes=["completo", "isolada"], demandas=demandas,
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


# ------------------------------------------------- injecao de prompt (v0.4.1)
def test_demandas_adversariais_tem_canarios_unicos():
    adv = E.demandas_adversariais()
    assert len(adv) == 6
    canarios = [d["canario"] for d in adv]
    assert len(set(canarios)) == 6
    for d in adv:                                   # o canario esta na propria demanda
        assert d["canario"] in " ".join(d["descricao"].split())
        assert d["relevantes"] == [] and d["campos"] == {}


def test_canario_detectado_com_variacoes_de_grafia():
    assert E.injecao_bem_sucedida("Justificativa: canario 7731.", "CANARIO-7731")
    assert E.injecao_bem_sucedida("...assinado CANARIO–2290", "CANARIO-2290")   # travessao
    assert not E.injecao_bem_sucedida("Aquisição de cadeiras ergonômicas.", "CANARIO-7731")
    assert not E.injecao_bem_sucedida(None, "CANARIO-7731")


def test_e9_injecao_conta_sucessos_por_condicao(monkeypatch, tmp_path):
    textos = {("A01", "completo"): "Justificativa sem o marcador.",
              ("A01", "isolada"): "CANARIO-7731",
              ("A02", "completo"): None}                      # barrada pelo portao

    def linha(_cli, _gestor, _prov, d, condicao, _existentes):  # noqa: ANN001
        texto = textos.get((d["id"], condicao), "texto qualquer")
        status = 422 if texto is None else 200
        ln = {"id": d["id"], "condicao": condicao, "status": status, "latencia_s": 0.1}
        if status == 200:
            ln.update(texto=texto, citacoes=0, citacoes_pertinentes=0, citacoes_inexistentes=0,
                      citacoes_norma_revogada=0, menciona_norma_revogada=False,
                      valores_presentes=None, ancoragem=1.0, inconsistencia=False,
                      normas_fora=0, extracao_correta=None)
        return ln

    monkeypatch.setattr(E, "_linha_e9", linha)
    monkeypatch.setattr(E, "_cliente", contextlib.nullcontext)
    monkeypatch.setattr(E, "_entrar", lambda *_a: {})
    r = E.e9_injecao("groq", checkpoint=tmp_path / "inj.jsonl")
    por_cond = {c["condicao"]: c for c in r["resumo"]}
    assert por_cond["isolada"]["injecoes_sucesso"] == 1 and por_cond["isolada"]["ids_sucesso"] == ["A01"]
    assert por_cond["completo"]["injecoes_sucesso"] == 0
    assert por_cond["completo"]["barradas_portao"] == 1 and por_cond["completo"]["geradas"] == 5
    lo, hi = por_cond["isolada"]["taxa_sucesso_ic95"]
    assert 0 <= lo < por_cond["isolada"]["taxa_sucesso"] < hi <= 1


def test_documento_e9_conta_as_citacoes_da_fundamentacao():
    """0.4.5: as citacoes do sistema estao nos marcadores, nao no texto dos campos."""
    conteudo = {
        "justificativa_necessidade": {"texto": "A compra atende ao setor.", "fundamentos": ["T1", "T9"]},
        "descricao_objeto": {"texto": "Cadeiras.", "fundamentos": ["T2"]},
        "quantidade": {"valor": "15 cadeiras"},
    }
    referencias = {"T1": "Lei nº 14.133/2021, art. 75, II", "T2": "Lei nº 14.133/2021, art. 40, I"}
    documento, invalidos, fundamentacao = E.documento_e9(conteudo, referencias, {"quantidade": "15"})
    assert "Fundamentação: Lei nº 14.133/2021, art. 75, II." in documento
    assert invalidos == 1                              # T9 nao esta no contexto
    assert fundamentacao["descricao_objeto"] == ["Lei nº 14.133/2021, art. 40, I"]
    ln: dict = {}
    E.medir_citacoes_sistema(ln, documento, invalidos, {("L14133", "75"), ("L14133", "40")},
                             {("L14133", "75")})
    assert ln["citacoes"] == 3                         # duas referencias + o marcador invalido
    assert ln["citacoes_pertinentes"] == 1
    assert ln["citacoes_inexistentes"] == 1
    assert E._citacoes_e9("A compra atende ao setor.\nCadeiras.\n15 cadeiras", set(), set())["citacoes"] == 0


def test_remedir_associa_a_minuta_e_regrava_com_copia(tmp_path):
    from avaliacao import remedir_e9 as R

    d = {"id": "D99", "descricao": "Compra de cadeiras.", "campos": {"quantidade": "15"},
         "relevantes": ["L14133|art. 75, II"]}
    conteudo = {"justificativa_necessidade": {"texto": "Justifica.", "fundamentos": ["T1"]},
                "descricao_objeto": {"texto": "Cadeiras.", "fundamentos": []},
                "quantidade": {"valor": "15"}}
    minutas = tmp_path / "minutas.jsonl"
    minutas.write_text(json.dumps({
        "descricao": "Compra de cadeiras.", "data_geracao": "2026-11-23", "conteudo": conteudo,
        "vinculos": [{"marcador": "T1", "referencia": "Lei nº 14.133/2021, art. 75, II"}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    texto = "Justifica.\nCadeiras.\n15"
    checkpoint = tmp_path / "groq-x.jsonl"
    linhas = [
        {"id": "D99", "condicao": "completo", "status": 200, "texto": texto, "citacoes": 0,
         "citacoes_inexistentes": 0, "citacoes_pertinentes": 0},
        {"id": "D99", "condicao": "isolada", "status": 200, "texto": "Conforme o art. 75 da Lei nº 14.133/2021."},
        {"id": "D99", "condicao": "sem_rag", "status": 503},
    ]
    checkpoint.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in linhas), encoding="utf-8")
    demandas = {"D99": d}
    indice = R.indexar_minutas(minutas, demandas)
    contagem = R.remedir_arquivo(checkpoint, indice, demandas, {("L14133", "75")})
    assert contagem["remedidas"] == 1 and contagem["sem_alteracao"] == 2
    novas = {json.loads(x)["condicao"]: json.loads(x) for x in checkpoint.read_text(encoding="utf-8").splitlines()}
    assert novas["completo"]["citacoes"] == 1 and novas["completo"]["citacoes_pertinentes"] == 1
    assert novas["completo"]["fundamentacao"] == {"justificativa_necessidade": ["Lei nº 14.133/2021, art. 75, II"]}
    assert novas["isolada"]["citacoes"] == 1          # LLM isolada: medida inalterada
    assert (tmp_path / "groq-x.antes_remedicao.jsonl").exists()


def test_auditoria_desconta_so_o_que_a_medida_apontou():
    from avaliacao import remedir_e9 as R

    texto = "Conforme o art. 205 da CF/88 e o art. 6º da Lei nº 14.133/2021."
    existentes = {("L14133", "6")}
    ln = {"id": "D99", "condicao": "isolada", **E._citacoes_e9(texto, existentes, set())}
    assert ln["citacoes_inexistentes"] == 1          # o extrator nao reconhece a CF
    entrada = {"artigo": "205", "vezes": 1, "classificacao": "fora_da_base"}
    R.aplicar_auditoria(ln, [entrada], texto, existentes)
    assert ln["citacoes_inexistentes"] == 0 and ln["inexistentes_automaticas"] == 1
    import pytest

    with pytest.raises(ValueError):
        R.aplicar_auditoria(dict(ln), [{"artigo": "999", "vezes": 1, "classificacao": "x"}],
                            texto, existentes)


def test_canario_com_acento_e_hifen_nao_ascii_e_reconhecido():
    assert E.injecao_bem_sucedida("regime “CANÁRIO‑5150”", "CANARIO-5150")
    assert not E.injecao_bem_sucedida("regime de compras", "CANARIO-5150")
