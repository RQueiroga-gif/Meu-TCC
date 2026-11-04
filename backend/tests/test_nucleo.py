"""Testes da minimizacao de dados pessoais (RN06) e da verificacao (RN02)."""
from __future__ import annotations

import pytest

from app.core.anonimizador import contem_dado_pessoal, minimizar
from app.core.verificacao import pendencias, verificar


# --------------------------------------------------------------- RN06
def test_remove_identificadores_diretos():
    texto = (
        "Contatar o servidor João pelo CPF 123.456.789-09, e-mail joao@orgao.gov.br "
        "ou telefone (45) 99999-1234. Fornecedor CNPJ 12.345.678/0001-90."
    )
    r = minimizar(texto)
    assert "[CPF]" in r.texto and "[EMAIL]" in r.texto
    assert "[TELEFONE]" in r.texto and "[CNPJ]" in r.texto
    assert "123.456.789-09" not in r.texto
    assert r.ocorrencias == {"CNPJ": 1, "CPF": 1, "EMAIL": 1, "TELEFONE": 1}


def test_cnpj_nao_e_confundido_com_cpf():
    """O CNPJ e tratado antes do CPF; caso contrario o padrao de CPF o quebraria."""
    r = minimizar("Fornecedor CNPJ 12.345.678/0001-90 habilitado.")
    assert r.texto == "Fornecedor CNPJ [CNPJ] habilitado."
    assert "CPF" not in r.ocorrencias


def test_texto_sem_dado_pessoal_permanece_intacto():
    texto = "Aquisição de 40 notebooks para o setor de protocolo."
    r = minimizar(texto)
    assert r.texto == texto
    assert not r.houve_remocao
    assert not contem_dado_pessoal(texto)


@pytest.mark.parametrize("entrada", ["", None])
def test_entradas_vazias(entrada):
    assert minimizar(entrada or "").texto == ""


# --------------------------------------------------------------- RN02
CONTEXTO = [
    {"marcador": "T1", "referencia": "Lei nº 14.133/2021, art. 18, I", "texto": "art. 18 descrição"},
    {"marcador": "T2", "referencia": "Decreto nº 10.947/2022, art. 8º, I", "texto": "art. 8 justificativa"},
]
DEMANDA = "Aquisição de 40 notebooks para os laboratórios, prioridade alta."


def test_minuta_integra_tem_ancoragem_total():
    minuta = {
        "justificativa_necessidade": {"texto": "Justifica-se a contratação.", "fundamentos": ["T1"]},
        "descricao_objeto": {"texto": "Aquisição de equipamentos.", "fundamentos": ["T2"]},
        "quantidade": {"valor": "40 notebooks", "evidencia": "40 notebooks"},
    }
    r = verificar(minuta, CONTEXTO, DEMANDA)
    assert r.taxa_ancoragem == 1.0
    assert not r.inconsistencia_detectada
    assert r.citacoes_invalidas == 0


def test_detecta_fundamento_inexistente():
    minuta = {
        "justificativa_necessidade": {"texto": "Texto.", "fundamentos": ["T1", "T9"]},
        "descricao_objeto": {"texto": "Texto.", "fundamentos": ["T2"]},
    }
    r = verificar(minuta, CONTEXTO, DEMANDA)
    assert r.inconsistencia_detectada
    assert r.por_campo["justificativa_necessidade"]["fundamentos_invalidos"] == ["T9"]
    assert r.citacoes_invalidas == 1


def test_detecta_artigo_fora_do_contexto():
    """Alucinacao normativa: o texto cita dispositivo que nao foi recuperado."""
    minuta = {
        "justificativa_necessidade": {
            "texto": "Conforme o art. 40 da Lei, a contratação é necessária.",
            "fundamentos": ["T1"],
        }
    }
    r = verificar(minuta, CONTEXTO, DEMANDA)
    assert r.inconsistencia_detectada
    assert "40" in r.por_campo["justificativa_necessidade"]["artigos_fora_do_contexto"]


def test_detecta_extracao_sem_evidencia():
    """Valor inventado: a evidencia nao aparece na demanda."""
    minuta = {"estimativa_valor": {"valor": "R$ 150.000,00", "evidencia": "R$ 150.000,00"}}
    r = verificar(minuta, CONTEXTO, DEMANDA)
    assert r.inconsistencia_detectada
    assert r.por_campo["estimativa_valor"]["extracao_sem_evidencia"]


def test_aceita_extracao_com_evidencia_literal():
    minuta = {"quantidade": {"valor": "40 unidades", "evidencia": "40 notebooks"}}
    r = verificar(minuta, CONTEXTO, DEMANDA)
    assert not r.por_campo["quantidade"]["extracao_sem_evidencia"]


def test_evidencia_ignora_acento_e_caixa():
    minuta = {"grau_prioridade": {"valor": "alto", "evidencia": "PRIORIDADE ALTA"}}
    r = verificar(minuta, CONTEXTO, DEMANDA)
    assert not r.por_campo["grau_prioridade"]["extracao_sem_evidencia"]


def test_campo_redigido_sem_fundamento_e_sinalizado():
    minuta = {"justificativa_necessidade": {"texto": "Texto sem lastro.", "fundamentos": []}}
    r = verificar(minuta, CONTEXTO, DEMANDA)
    assert r.inconsistencia_detectada
    assert r.por_campo["justificativa_necessidade"]["sem_fundamento"]
    assert r.taxa_ancoragem == 0.0


def test_valor_declarado_pelo_agente_nao_exige_evidencia():
    minuta = {"estimativa_valor": {"valor": "R$ 90.000,00", "evidencia": None, "origem": "agente"}}
    r = verificar(minuta, CONTEXTO, DEMANDA)
    assert not r.inconsistencia_detectada


# --------------------------------------------------------------- analise
def test_analise_aponta_pendencias_e_alertas():
    from app.core.analise import analisar

    minuta = {
        "justificativa_necessidade": {"texto": "Curto.", "fundamentos": ["T1"]},
        "descricao_objeto": {"texto": "Aquisição de 40 notebooks.", "fundamentos": ["T2"]},
        "quantidade": {"valor": "40 notebooks", "evidencia": "40 notebooks"},
        "grau_prioridade": {"valor": "urgentíssima", "evidencia": None, "origem": "agente"},
        "data_pretendida": {"valor": "01/01/2020", "evidencia": None, "origem": "agente"},
    }
    fontes = [dict(c, similaridade=0.5) for c in CONTEXTO]
    r = analisar(minuta, fontes, DEMANDA)
    situacao = {c["campo"]: c["situacao"] for c in r["checklist"]}
    assert situacao["quantidade"] == "ok"
    assert situacao["justificativa_necessidade"] == "alerta"      # texto sucinto
    assert situacao["grau_prioridade"] == "alerta"                # fora de baixo/medio/alto
    assert situacao["data_pretendida"] == "alerta"                # data no passado
    assert situacao["estimativa_valor"] == "pendente"
    assert r["pronto_para_validacao"] is False
    assert r["indicadores"]["fontes_citadas"] == 2
    assert len(r["checklist"]) == 8


# --------------------------------------------------------------- indice
def test_indice_recusa_gerador_diferente(tmp_path):
    from app.core.embeddings import EmbedderHash, obter_embedder, redefinir_embedder
    from app.core.indice import IndiceIncompativel, IndiceNumpy

    original = obter_embedder()
    try:
        redefinir_embedder(EmbedderHash(dimensao=64))
        indice = IndiceNumpy(tmp_path / "i.npz")
        indice.indexar([{"referencia_vetorial": "X|1", "referencia": "r", "texto_indexado": "texto"}])

        redefinir_embedder(EmbedderHash(dimensao=128))
        recarregado = IndiceNumpy(tmp_path / "i.npz")
        with pytest.raises(IndiceIncompativel):
            recarregado.buscar("texto", k=1)
        assert recarregado.faltantes(["X|1", "X|2"]) == ["X|2"]
    finally:
        redefinir_embedder(original)


def test_indice_atualiza_vigencia_sem_reindexar(tmp_path):
    from app.core.indice import IndiceNumpy

    indice = IndiceNumpy(tmp_path / "i.npz")
    indice.indexar(
        [
            {"referencia_vetorial": "A|1", "referencia": "a", "texto_indexado": "planejamento anual"},
            {"referencia_vetorial": "B|1", "referencia": "b", "texto_indexado": "planejamento anual"},
        ]
    )
    assert indice.atualizar_vigencia(["A|1"], False) == 1
    refs = {o.referencia_vetorial for o in IndiceNumpy(tmp_path / "i.npz").buscar("planejamento", 5)}
    assert refs == {"B|1"}          # RN03 persistido: revogado nao volta apos recarga


def test_pendencias_lista_campos_nulos():
    minuta = {
        "quantidade": {"valor": "40 notebooks", "evidencia": "40 notebooks"},
        "estimativa_valor": {"valor": None, "evidencia": None},
        "data_pretendida": {"valor": None, "evidencia": None},
    }
    assert set(pendencias(minuta)) == {"estimativa_valor", "data_pretendida"}
