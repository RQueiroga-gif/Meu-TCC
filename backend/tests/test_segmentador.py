"""Testes do segmentador normativo (F1).

Reproduzem as convencoes reais do HTML do Planalto. O caso do caput com alineas
corresponde a um defeito encontrado na revisao de 29/09/2026: alineas ligadas
diretamente ao caput eram descartadas silenciosamente.
"""
from __future__ import annotations

import pytest

from app.core.segmentador import decodificar, segmentar

NORMA = "Lei nº 14.133/2021"


def envolver(corpo: str) -> str:
    return f"<html><body>{corpo}</body></html>"


def referencias(html: str) -> list[str]:
    return [d.referencia for d in segmentar(html, NORMA)]


def test_artigo_incisos_e_paragrafo():
    html = envolver(
        "<p>Art. 18. A fase preparatória é caracterizada pelo planejamento.</p>"
        "<p>I - a descrição da necessidade da contratação;</p>"
        "<p>II - a definição do objeto;</p>"
        "<p>§ 1º O estudo técnico preliminar deverá evidenciar o problema.</p>"
        "<p>I - descrição da necessidade;</p>"
    )
    assert referencias(html) == [
        "art. 18, caput",
        "art. 18, I",
        "art. 18, II",
        "art. 18, § 1º",
        "art. 18, § 1º, I",
    ]


def test_descarta_vetado_e_revogado():
    html = envolver(
        "<p>Art. 20. Texto do caput.</p>"
        "<p>I - inciso válido;</p>"
        "<p>II - (VETADO);</p>"
        "<p>Art. 21. (Revogado pela Lei nº 14.770, de 2023)</p>"
        "<p>Parágrafo único. Texto do parágrafo único.</p>"
    )
    refs = referencias(html)
    assert "art. 20, II" not in refs        # vetado
    assert "art. 21, caput" not in refs     # revogado
    assert "art. 21, parágrafo único" in refs


def test_descarta_texto_tachado_e_anotacoes():
    html = envolver(
        "<p>Art. 75. <strike>São dispensados os procedimentos</strike> "
        "<span>(Vide Lei nº 14.628, de 2023)</span></p>"
        "<p>I - contratação de valor inferior a R$ 100.000,00; "
        "<span>(Incluído pela Lei nº 14.770, de 2023)</span></p>"
    )
    dispositivos = segmentar(html, NORMA)
    assert [d.referencia for d in dispositivos] == ["art. 75, I"]
    assert "Vide" not in dispositivos[0].texto
    assert "Incluído pela" not in dispositivos[0].texto


def test_alinea_sob_inciso_vai_para_o_inciso():
    html = envolver(
        "<p>Art. 6. Para os fins desta Lei, consideram-se:</p>"
        "<p>I - órgão: unidade de atuação integrante da Administração;</p>"
        "<p>a) na Administração direta, os Ministérios;</p>"
        "<p>b) na Administração indireta, as autarquias;</p>"
    )
    dispositivos = {d.referencia: d for d in segmentar(html, NORMA)}
    assert len(dispositivos["art. 6º, I"].alineas) == 2
    assert dispositivos["art. 6º, caput"].alineas == []


def test_alinea_sob_caput_nao_se_perde():
    """Regressao: antes da correcao de 29/09/2026 estas alineas eram descartadas."""
    html = envolver(
        "<p>Art. 25. O edital deverá conter o objeto, observado o seguinte:</p>"
        "<p>a) o prazo de vigência contratual;</p>"
        "<p>b) a forma de pagamento;</p>"
    )
    dispositivos = {d.referencia: d for d in segmentar(html, NORMA)}
    assert len(dispositivos["art. 25, caput"].alineas) == 2


def test_alinea_orfa_de_inciso_vetado_e_descartada():
    html = envolver(
        "<p>Art. 30. Caput do artigo.</p>"
        "<p>II - (VETADO);</p>"
        "<p>a) alínea do inciso vetado;</p>"
    )
    dispositivos = segmentar(html, NORMA)
    assert all(not d.alineas for d in dispositivos)


def test_artigo_com_letra_e_inciso_alto():
    html = envolver(
        "<p>Art. 6-A. Aplica-se o disposto nesta Lei.</p>"
        "<p>LX - contratação integrada: regime de contratação;</p>"
    )
    assert referencias(html) == ["art. 6º-A, caput", "art. 6º-A, LX"]


def test_contexto_prefixa_o_inciso():
    html = envolver(
        "<p>Art. 18. A fase preparatória é caracterizada pelo planejamento.</p>"
        "<p>I - a descrição da necessidade da contratação;</p>"
    )
    inciso = [d for d in segmentar(html, NORMA) if d.referencia == "art. 18, I"][0]
    indexado = inciso.texto_indexado()
    assert indexado.startswith("Lei nº 14.133/2021, art. 18, I.")
    assert "fase preparatória" in indexado          # contexto do caput preservado
    assert "descrição da necessidade" in indexado


def test_decodifica_cp1252_do_planalto():
    bruto = "<html><body><p>Art. 1. Da formalização da demanda.</p></body></html>".encode(
        "cp1252"
    )
    dispositivos = segmentar(decodificar(bruto), NORMA)
    assert "formalização" in dispositivos[0].texto


def test_html_sem_artigos_devolve_lista_vazia():
    assert segmentar(envolver("<p>Texto qualquer sem dispositivos.</p>"), NORMA) == []


@pytest.mark.parametrize("entrada", ["", "<html></html>", "<html><body></body></html>"])
def test_entradas_degeneradas(entrada: str):
    assert segmentar(entrada, NORMA) == []


# ---------------------------------------------------------------------------
# Casos encontrados na avaliacao com o HTML oficial integral (26/10/2026)
# ---------------------------------------------------------------------------
def test_descarta_tachado_por_estilo_css():
    """O Planalto tambem marca redacao superada com text-decoration: line-through."""
    html = envolver(
        "<p>Art. 5º Para os fins desta Lei, considera-se:</p>"
        '<p><span style="color: black; text-decoration:line-through">'
        "VIII - encarregado: redação original superada;</span></p>"
        "<p>VIII - encarregado: redação vigente;</p>"
    )
    dispositivos = segmentar(html, NORMA)
    textos = [d.texto for d in dispositivos if d.referencia == "art. 5º, VIII"]
    assert textos == ["encarregado: redação vigente;"]


def test_paragrafo_e_inciso_com_letra():
    html = envolver(
        "<p>Art. 174. Texto do caput.</p>"
        "<p>§ 3º O PNCP deverá oferecer:</p>"
        "<p>§ 3º-A. As funcionalidades serão adotadas pelo Poder Executivo.</p>"
        "<p>Art. 175. Texto.</p>"
        "<p>XVI-A - inciso acrescido;</p>"
    )
    refs = referencias(html)
    assert "art. 174, § 3º" in refs and "art. 174, § 3º-A" in refs
    assert "art. 175, XVI-A" in refs


def test_transcricao_de_outra_norma_nao_e_atribuida():
    """Arts. 337-E e seguintes do Codigo Penal, transcritos no art. 178."""
    html = envolver(
        "<p>Art. 178. O Código Penal passa a vigorar acrescido do seguinte Capítulo:</p>"
        "<p>“CAPÍTULO II-B</p>"
        "<p>Art. 337-E. Admitir contratação direta fora das hipóteses legais:</p>"
        "<p>I - inciso do Código Penal;</p>"
        "<p>Art. 179. O art. 2º da Lei nº 8.987 passa a vigorar com a seguinte redação:</p>"
        "<p>“Art. 2º ..................................</p>"
        "<p>II - concessão de serviço público: delegação;</p>"
        "<p>.............................................” (NR)</p>"
        "<p>Art. 180. Texto próprio.</p>"
        "<p>I - inciso próprio;</p>"
    )
    refs = referencias(html)
    assert not any("337" in r for r in refs)
    assert "art. 179, II" not in refs            # inciso da Lei nº 8.987 transcrito
    assert refs[-2:] == ["art. 180, caput", "art. 180, I"]


def test_html_malformado_nao_trunca_dispositivo():
    """</font> fechando dentro do <p>: caso real do Decreto nº 10.947/2022, art. 7º, III.

    O paragrafo esta dentro de um <font> aberto antes dele; o </font> interno
    fecha esse elemento externo. O lxml encerra o <p> nesse ponto e perde o
    restante do inciso; o html5lib o reconstroi como um navegador.
    """
    html = (
        '<html><body><font face="Arial" size="2">'
        "<p>Art. 7º Ficam dispensadas de registro no plano:</p>"
        '<p><span><a name="art7iii"></a>III - as hipóteses previstas nos '
        '<a href="#art75vi">incisos VI</a></span></font><font size="2">, </font>'
        '<font size="2"><span><a href="#art75vii">VII</a></span></font>'
        '<font size="2"><span> e </span></font><font size="2"><span>'
        '<a href="#art75viii">VIII do <b>caput</b> do art. 75 da Lei nº 14.133, de 2021;</a> e'
        "</span></p></font></body></html>"
    )
    textos = {d.referencia: d.texto for d in segmentar(html, NORMA)}
    assert textos["art. 7º, III"].endswith("VII e VIII do caput do art. 75 da Lei nº 14.133, de 2021; e")
