"""Tela 1 — Registro da necessidade administrativa (UC02, RF02, RN06).

Também atende o fluxo A1: quando a geração é barrada por falta de fundamento, ou
quando o agente escolhe "Ajustar demanda", a tela edita a MESMA demanda em vez de
abrir um DFD novo.
"""
from __future__ import annotations

import streamlit as st

import cliente

st.set_page_config(page_title="Registrar demanda", layout="wide")
usuario = cliente.exigir_sessao()

id_existente = st.session_state.get("id_dfd") if st.session_state.get("modo_ajuste") else None
atual: dict = {}
if id_existente:
    try:
        atual = cliente.obter_demanda(id_existente)
    except cliente.ErroAPI as erro:
        st.error(str(erro))
        st.stop()

st.progress(1 / 3, text="Etapa 1 de 3 — Registro da demanda")
st.title("Ajuste da demanda" if atual else "Registro da necessidade administrativa")
if atual:
    st.caption(f"Editando a demanda do DFD {id_existente}. As minutas anteriores ficam no histórico.")

with st.form("demanda"):
    col_a, col_b = st.columns(2)
    unidade = col_a.text_input(
        "Unidade requisitante",
        value=atual.get("unidade_requisitante") or usuario.get("unidade") or "",
        placeholder="Coordenadoria de Protocolo",
    )
    col_b.text_input("Responsável pela demanda", value=usuario["nome"], disabled=True)

    titulo = st.text_input(
        "Título da demanda",
        value=atual.get("titulo", ""),
        placeholder="Aquisição de equipamentos de informática para o setor de protocolo",
    )
    descricao = st.text_area(
        "Descrição da necessidade administrativa",
        value=atual.get("descricao", ""),
        height=220,
        placeholder=(
            "Descreva o problema, a quantidade estimada, o valor estimado, a data "
            "pretendida e a prioridade. O sistema recuperará os fundamentos "
            "normativos aplicáveis e só extrai valores que estiverem escritos aqui."
        ),
    )
    st.caption(
        f"{len(descricao.strip())} caracteres · dica: informe quantidade, valor (R$), "
        "data pretendida (dd/mm/aaaa) e prioridade (baixa, média ou alta)."
    )

    st.warning(
        "Evite inserir dados pessoais desnecessários na descrição. Identificadores "
        "como CPF, CNPJ, e-mail e telefone são removidos automaticamente antes de "
        "qualquer processamento, e todas as informações registradas são gravadas em "
        "log de auditoria.",
        icon=":material/privacy_tip:",
    )

    col_rascunho, col_gerar = st.columns([1, 1])
    salvar = col_rascunho.form_submit_button("Salvar rascunho", width="stretch")
    gerar = col_gerar.form_submit_button(
        "Gerar minuta preliminar", type="primary", width="stretch"
    )

if salvar or gerar:
    if len(descricao.strip()) < 20:
        st.error("Descreva a necessidade com mais detalhes (mínimo de 20 caracteres).")
        st.stop()
    if len(titulo.strip()) < 5 or len(unidade.strip()) < 2:
        st.error("Informe o título da demanda (mínimo de 5 caracteres) e a unidade requisitante.")
        st.stop()

    try:
        if id_existente:
            demanda = cliente.ajustar_demanda(id_existente, unidade, titulo, descricao)
        else:
            demanda = cliente.registrar_demanda(unidade, titulo, descricao)
    except cliente.ErroAPI as erro:
        st.error(str(erro))
        st.stop()

    # A partir daqui, qualquer nova submissao ajusta esta mesma demanda.
    st.session_state["id_dfd"] = demanda["id_dfd"]
    st.session_state["modo_ajuste"] = True
    st.session_state.pop("minuta", None)

    if removidos := demanda.get("dados_pessoais_removidos"):
        detalhe = ", ".join(f"{qtd} {rotulo}" for rotulo, qtd in removidos.items())
        st.info(
            f"Identificadores removidos antes da gravação: {detalhe}.",
            icon=":material/shield:",
        )

    if salvar:
        st.success("Rascunho salvo. Você pode continuar editando ou gerar a minuta depois.")
        st.stop()

    # ------------------------------------------------ geração (UC03 + UC04)
    with st.status("Elaborando a minuta…", expanded=True) as estado:
        st.write("Interpretando a demanda registrada")
        st.write("Buscando fundamentos na base normativa")
        try:
            minuta = cliente.gerar_minuta(demanda["id_dfd"])
        except cliente.ErroAPI as erro:
            estado.update(label="Não foi possível gerar a minuta", state="error")
            if erro.codigo == 422:
                st.error(str(erro), icon=":material/search_off:")
                st.caption(
                    "A demanda foi preservada. Reformule a descrição acima, informando o "
                    "objeto e a finalidade da contratação, e gere novamente."
                )
            else:
                st.error(str(erro), icon=":material/cloud_off:")
            st.stop()

        st.write(f"Contexto montado com {len(minuta['fontes'])} trecho(s) normativo(s)")
        st.write("Minuta redigida, verificada e analisada")
        estado.update(label="Minuta gerada", state="complete")

    st.session_state["minuta"] = minuta
    st.session_state.pop("modo_ajuste", None)
    st.switch_page("pages/2_Revisar_minuta.py")
