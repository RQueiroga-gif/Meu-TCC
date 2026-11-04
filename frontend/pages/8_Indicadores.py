"""Painel do gestor — indicadores de uso, qualidade da geração e governança.

Os números vêm de GET /indicadores, calculados a partir da trilha de auditoria e
das tabelas do ciclo do DFD. Alimentam diretamente o capítulo de resultados.
"""
from __future__ import annotations

import streamlit as st

import cliente

st.set_page_config(page_title="Indicadores", layout="wide")
usuario = cliente.exigir_sessao()

st.title("Indicadores do sistema")
if usuario["perfil"] != "gestor":
    st.info("Painel restrito ao perfil gestor.")
    st.stop()

try:
    dados = cliente.indicadores()
except cliente.ErroAPI as erro:
    st.error(str(erro))
    st.stop()


def pct(valor: float | None) -> str:
    return f"{valor:.0%}" if valor is not None else "—"


dfds, geracao, revisao, validacao = (
    dados["dfds"], dados["geracao"], dados["revisao"], dados["validacao"]
)

st.subheader("Documentos")
c = st.columns(4)
c[0].metric("DFDs registrados", dfds["total"])
c[1].metric("Em elaboração", dfds["por_situacao"].get("em_elaboracao", 0))
c[2].metric("Validados", dfds["por_situacao"].get("validado", 0))
c[3].metric("Exportados", dfds["exportados"])

st.subheader("Geração assistida")
c = st.columns(4)
c[0].metric("Tentativas de geração", geracao["tentativas"])
c[1].metric("Taxa de sucesso", pct(geracao["taxa_sucesso"]))
c[2].metric("Barradas por falta de fundamento", geracao["barradas_por_suficiencia"],
            help="Fluxo A1: o portão de suficiência impediu gerar texto sem lastro.")
c[3].metric("Falhas do provedor", geracao["falhas_do_provedor"])

c = st.columns(4)
c[0].metric("Ancoragem média", pct(geracao["ancoragem_media"]),
            help="Fração dos campos redigidos com fundamento válido no contexto.")
c[1].metric("Ancoragem mínima", pct(geracao["ancoragem_minima"]))
c[2].metric("Com inconsistência", pct(geracao["taxa_inconsistencia"]))
c[3].metric("Prontidão média", pct(geracao["prontidao_media"]))

tempo = geracao.get("tempo_mediano_ms")
st.metric("Tempo mediano da geração", f"{tempo / 1000:.2f} s".replace(".", ",") if tempo is not None else "—",
          help="Da recuperação à gravação da versão, medido em cada geração (RNF04).")

if geracao["por_modelo"]:
    st.caption("Gerações por modelo")
    st.bar_chart(geracao["por_modelo"], horizontal=True)

st.subheader("Revisão e validação humana")
c = st.columns(4)
c[0].metric("Edições humanas", revisao["edicoes_humanas"])
media = revisao["versoes_media_por_dfd"]
c[1].metric("Versões por DFD (média)", f"{media:.1f}" if media is not None else "—")
c[2].metric("Aprovações / devoluções", f"{validacao['aprovacoes']} / {validacao['devolucoes']}")
horas = validacao["horas_ate_aprovar_mediana"]
c[3].metric("Tempo até aprovar (mediana)", f"{horas:.1f} h" if horas is not None else "—")

st.caption(
    "Com o provedor 'echo', estes números servem apenas para validar o fluxo. As "
    "métricas do capítulo de resultados vêm do roteiro de avaliação "
    "(python -m avaliacao.executar, na pasta backend)."
)
