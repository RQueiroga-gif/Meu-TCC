"""Verificacao com norma qualificada (versao 0.3, avaliacao de 26/10/2026).

Cada teste corresponde a um tipo de falha do experimento de injecao de falhas
(avaliacao/experimentos.py) que a versao 0.2.0 deixava passar.
"""
from __future__ import annotations

from app.core.verificacao import mencoes, normas_mencionadas, verificar

CONTEXTO = [
    {
        "marcador": "T1",
        "referencia": "Lei nº 14.133/2021, art. 8º, caput",
        "texto": "Lei nº 14.133/2021, art. 8º, caput. A licitação será conduzida por agente "
        "de contratação.",
    },
    {
        "marcador": "T2",
        "referencia": "Decreto nº 10.947/2022, art. 7º, IV",
        "texto": "Decreto nº 10.947/2022, art. 7º, IV. As pequenas compras de que trata o "
        "§ 2º do art. 95 da Lei nº 14.133, de 2021.",
    },
]


def _campo(texto: str) -> dict:
    return {"justificativa_necessidade": {"texto": texto, "fundamentos": ["T1"]}}


def _problemas(texto: str) -> dict:
    return verificar(_campo(texto), CONTEXTO, "demanda").por_campo["justificativa_necessidade"]


def test_mencoes_no_plural_e_com_norma():
    assert mencoes("arts. 74 e 75 da Lei nº 14.133/2021") == [("74", "14133"), ("75", "14133")]
    assert mencoes("art. 6º-A da LGPD") == [("6-a", "13709")]
    assert mencoes("art. 18, § 1º, I") == [("18", None)]


def test_mesmo_numero_em_outra_norma_e_apontado():
    """art. 8º existe no contexto, mas da Lei, nao do Decreto."""
    p = _problemas("Conforme o art. 8º do Decreto nº 10.947/2022.")
    assert p["artigos_fora_do_contexto"] == ["8"]


def test_mesmo_numero_na_norma_certa_e_aceito():
    p = _problemas("Conforme o art. 8º da Lei nº 14.133/2021.")
    assert p["artigos_fora_do_contexto"] == []
    assert p["normas_fora_do_contexto"] == []


def test_mencao_dentro_do_trecho_conta_como_contexto():
    """O trecho T2 cita o art. 95 da Lei nº 14.133: a minuta pode menciona-lo."""
    p = _problemas("Pequenas compras, nos termos do art. 95 da Lei nº 14.133/2021.")
    assert p["artigos_fora_do_contexto"] == []


def test_norma_revogada_e_apontada():
    p = _problemas("Fundamenta-se na Lei nº 8.666/1993 e no art. 8º da Lei nº 14.133/2021.")
    assert p["normas_fora_do_contexto"] == ["8666"]
    assert normas_mencionadas("LGPD e Decreto nº 10.947/2022") == {"13709", "10947"}


def test_plural_fora_do_contexto_e_apontado():
    p = _problemas("Hipóteses dos arts. 74 e 75 da Lei nº 14.133/2021.")
    assert p["artigos_fora_do_contexto"] == ["74", "75"]
