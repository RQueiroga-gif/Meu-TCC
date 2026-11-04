"""Assistente de DFD — acesso ao sistema e painel do agente público.

Execução:
    streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

import cliente

st.set_page_config(
    page_title="Assistente de DFD",
    page_icon=":material/description:",
    layout="wide",
)

ETIQUETA_SITUACAO = {
    "em_elaboracao": ("Em elaboração", "blue"),
    "validado": ("Validado", "green"),
    "exportado": ("Exportado", "violet"),
    "rascunho": ("Rascunho", "gray"),
}


def tela_de_acesso() -> None:
    """UC01 — autenticação."""
    _, centro, _ = st.columns([1, 1.2, 1])
    with centro:
        st.title("Assistente de DFD")
        st.caption(
            "Apoio à elaboração do Documento de Formalização da Demanda, "
            "com fundamentação normativa recuperada e validação humana obrigatória."
        )
        with st.form("acesso"):
            email = st.text_input("E-mail ou usuário", placeholder="nome@orgao.gov.br")
            senha = st.text_input("Senha", type="password")
            enviado = st.form_submit_button("Entrar", type="primary", width="stretch")

        if enviado:
            if not email or not senha:
                st.error("Informe e-mail e senha.")
                return
            try:
                dados = cliente.entrar(email.strip(), senha)
            except cliente.ErroAPI as erro:
                st.error(str(erro))
                return
            st.session_state["token"] = dados["token"]
            st.session_state["usuario"] = dados
            st.rerun()

        st.caption(
            "Acesso restrito a agentes públicos autorizados. Todas as ações "
            "realizadas no sistema são registradas em log de auditoria."
        )


def painel() -> None:
    """Lista os DFDs do usuário e permite abrir cada um."""
    usuario = st.session_state["usuario"]
    cliente.exibir_aviso_pendente()

    with st.sidebar:
        st.subheader(usuario["nome"])
        st.caption(f"{usuario.get('unidade') or '—'} · perfil: {usuario['perfil']}")
        if st.button("Sair", width="stretch"):
            st.session_state.clear()
            st.rerun()

    cabecalho, acao = st.columns([3, 1])
    cabecalho.title(
        "Documentos de formalização da demanda"
        if usuario["perfil"] == "gestor"
        else "Meus documentos de formalização da demanda"
    )
    if acao.button("Nova demanda", type="primary", width="stretch"):
        for chave in ("id_dfd", "minuta", "modo_ajuste"):
            st.session_state.pop(chave, None)
        st.switch_page("pages/1_Registrar_demanda.py")

    try:
        documentos = cliente.listar_dfds()
    except cliente.ErroAPI as erro:
        st.error(str(erro))
        return

    if not documentos:
        st.info("Nenhuma demanda registrada ainda.")
        return

    # ------------------------------------------------ resumo e filtros
    contagem = {s: sum(1 for d in documentos if d["situacao"] == s) for s in ETIQUETA_SITUACAO}
    colunas = st.columns(4)
    colunas[0].metric("Total", len(documentos))
    colunas[1].metric("Em elaboração", contagem["em_elaboracao"])
    colunas[2].metric("Validados", contagem["validado"])
    colunas[3].metric("Exportados", contagem["exportado"])

    filtro_texto, filtro_situacao = st.columns([2, 1])
    busca = filtro_texto.text_input("Buscar", placeholder="número, título ou unidade",
                                    label_visibility="collapsed")
    situacoes = filtro_situacao.multiselect(
        "Situação",
        options=list(ETIQUETA_SITUACAO),
        format_func=lambda s: ETIQUETA_SITUACAO[s][0],
        placeholder="Todas as situações",
        label_visibility="collapsed",
    )
    termo = busca.strip().lower()
    visiveis = [
        d for d in documentos
        if (not situacoes or d["situacao"] in situacoes)
        and (not termo or termo in f"{d['numero']} {d['titulo']} {d['unidade_requisitante']}".lower())
    ]

    for doc in visiveis:
        rotulo, cor = ETIQUETA_SITUACAO.get(doc["situacao"], (doc["situacao"], "gray"))
        with st.container(border=True):
            col_info, col_estado, col_abrir = st.columns([5, 2, 1.4])
            col_info.markdown(f"**{doc['numero'] or '—'}** · {doc['titulo']}")
            detalhes = [
                doc["unidade_requisitante"],
                f"versão {doc['versao_atual'] or '—'}",
                f"criado em {doc['data_criacao'][:10]}",
            ]
            if usuario["perfil"] == "gestor" and doc.get("responsavel"):
                detalhes.insert(1, doc["responsavel"])
            col_info.caption(" · ".join(detalhes))
            col_estado.markdown(f":{cor}[{rotulo}]")
            if col_abrir.button("Abrir", key=f"abrir_{doc['id_dfd']}", width="stretch"):
                st.session_state["id_dfd"] = doc["id_dfd"]
                st.session_state.pop("minuta", None)
                if doc["versao_atual"] is None:
                    # Demanda sem minuta (rascunho ou barrada no fluxo A1).
                    st.session_state["modo_ajuste"] = True
                    st.switch_page("pages/1_Registrar_demanda.py")
                destino = (
                    "pages/3_Validar_e_exportar.py"
                    if doc["situacao"] in {"validado", "exportado"}
                    else "pages/2_Revisar_minuta.py"
                )
                st.switch_page(destino)


if st.session_state.get("token"):
    painel()
else:
    tela_de_acesso()
