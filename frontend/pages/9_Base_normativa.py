"""Tela do gestor — base normativa e registros de auditoria (UC09, UC10, UC11)."""
from __future__ import annotations

import streamlit as st

import cliente

st.set_page_config(page_title="Base normativa e auditoria", layout="wide")
usuario = cliente.exigir_sessao()
cliente.exibir_aviso_pendente()
gestor = usuario["perfil"] == "gestor"

st.title("Base normativa e auditoria")

base, diagnostico, registros = st.tabs(
    ["Base normativa", "Consulta à base", "Registros de auditoria"]
)

with base:
    try:
        stats = cliente.estatisticas_base()
        normas = cliente.listar_normas()
    except cliente.ErroAPI as erro:
        st.error(str(erro))
        st.stop()

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Normas vigentes", f"{stats['normas_vigentes']} de {stats['normas_total']}")
    col_b.metric("Trechos no banco", stats["trechos_indexados"])
    col_c.metric("Trechos no índice vetorial", stats["trechos_no_indice"])
    col_d.metric("Limiar de suficiência", f"{stats.get('limiar_efetivo', 0):.2f}")

    if stats["trechos_indexados"] != stats["trechos_no_indice"]:
        st.warning(
            "O banco e o índice vetorial estão dessincronizados. Reindexe a base "
            "antes de gerar novas minutas.",
            icon=":material/sync_problem:",
        )
    if gestor and st.button("Reindexar a base", icon=":material/sync:"):
        with st.spinner("Recalculando os vetores de todos os trechos…"):
            try:
                resultado = cliente.reindexar_base()
            except cliente.ErroAPI as erro:
                st.error(str(erro))
                st.stop()
        cliente.avisar_depois(f"Índice reconstruído: {resultado['trechos_indexados']} trechos.")
        st.rerun()

    for norma in normas:
        with st.container(border=True):
            info, estado = st.columns([5, 1.5])
            info.markdown(f"**{norma['tipo']} nº {norma['numero']}/{norma['ano']}** — {norma['ementa']}")
            indexada = (norma.get("data_indexacao") or "—")[:16].replace("T", " ")
            info.caption(
                f"Sigla {norma['sigla']} · indexada em {indexada} UTC"
                + (f" · [fonte oficial]({norma['url_fonte']})" if norma.get("url_fonte") else "")
            )
            if gestor:
                vigente = estado.toggle(
                    "Vigente", value=norma["vigente"], key=f"vig_{norma['id_norma']}",
                    help="Norma não vigente deixa de fundamentar minutas imediatamente (RN03).",
                )
                if vigente != norma["vigente"]:
                    try:
                        r = cliente.alterar_vigencia(norma["id_norma"], vigente)
                    except cliente.ErroAPI as erro:
                        st.error(str(erro))
                        st.stop()
                    cliente.avisar_depois(
                        f"{r['sigla']}: {'ativada' if vigente else 'desativada'} "
                        f"({r['trechos_afetados']} trechos)."
                    )
                    st.rerun()
            else:
                estado.markdown(":green[Vigente]" if norma["vigente"] else ":red[Não vigente]")

with diagnostico:
    st.caption(
        "Executa apenas a recuperação, sem gerar minuta — útil para conferir quais "
        "dispositivos uma demanda alcançaria e calibrar o limiar."
    )
    with st.form("busca"):
        consulta = st.text_area("Descrição de teste", height=100)
        k = st.slider("Quantidade de trechos", 1, 20, 6)
        buscar = st.form_submit_button("Consultar", type="primary")
    if buscar and len(consulta.strip()) >= 3:
        try:
            achados = cliente.buscar_normas(consulta, k)
        except cliente.ErroAPI as erro:
            st.error(str(erro))
            st.stop()
        limiar = achados.get("limiar_padrao", 0)
        st.caption(f"Limiar de suficiência em uso: {limiar:.2f}")
        for trecho in achados["trechos"]:
            acima = trecho["similaridade"] >= limiar
            with st.container(border=True):
                st.markdown(
                    f"**{trecho['referencia']}** · similaridade {trecho['similaridade']:.3f} "
                    + (":green[acima do limiar]" if acima else ":gray[abaixo do limiar]")
                )
                st.caption(trecho["texto"][:400])

with registros:
    if gestor:
        try:
            integridade = cliente.integridade_auditoria()
        except cliente.ErroAPI as erro:
            st.error(str(erro))
            integridade = None

        if integridade:
            if integridade["integra"]:
                st.success(
                    f"Cadeia de auditoria íntegra: {integridade['total']} registro(s) "
                    "verificado(s) por reprocessamento dos hashes.",
                    icon=":material/verified_user:",
                )
            else:
                st.error(
                    "Cadeia de auditoria rompida a partir do registro "
                    f"{integridade['primeiro_divergente']}. Há indício de adulteração.",
                    icon=":material/gpp_bad:",
                )
    else:
        st.caption("Você vê os registros das suas ações e dos seus documentos.")

    filtro_acao, filtro_dfd = st.columns(2)
    acao = filtro_acao.selectbox(
        "Ação",
        ["", "ACESSO", "REGISTRO_DEMANDA", "AJUSTE_DEMANDA", "GERACAO", "GERACAO_BARRADA",
         "FALHA_GERACAO", "EDICAO", "VALIDACAO", "EXPORTACAO", "ALTERACAO_VIGENCIA", "REINDEXACAO"],
        format_func=lambda a: a or "Todas",
    )
    dfd = filtro_dfd.number_input("DFD", min_value=0, step=1, help="0 = todos")

    try:
        eventos = cliente.auditoria(id_dfd=int(dfd) or None, limite=300, acao=acao or None)
    except cliente.ErroAPI as erro:
        st.error(str(erro))
        st.stop()

    st.dataframe(
        [
            {
                "Data e hora (UTC)": e["data_hora"][:19].replace("T", " "),
                # colunas homogeneas (texto): o Arrow rejeita misturar int e str
                "Usuário": str(e.get("usuario_nome") or e["id_usuario"]),
                "Ação": e["acao"],
                "DFD": str(e["id_dfd"]) if e["id_dfd"] else "—",
                "Detalhe": str(e["detalhe"] or "—")[:160],
                "Hash": e["hash_registro"][:12] + "…",
            }
            for e in eventos
        ],
        width="stretch",
        hide_index=True,
    )
