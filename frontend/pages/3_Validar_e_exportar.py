"""Tela 3 — Confirmação campo a campo, validação humana e exportação.

UC06, UC07 e UC08. A trava da RN01 aparece aqui como terceira camada: o botão de
exportação só é habilitado depois do registro da aprovação. As duas camadas
anteriores — o gatilho do banco e a verificação da API — continuam valendo mesmo
que esta seja contornada.
"""
from __future__ import annotations

import streamlit as st

import cliente

st.set_page_config(page_title="Validar e exportar", layout="wide")
usuario = cliente.exigir_sessao()
id_dfd = cliente.exigir_dfd()
cliente.exibir_aviso_pendente()

CAMPOS_ART_8 = cliente.ROTULOS_ART_8

try:
    minuta = cliente.obter_minuta(id_dfd)
except cliente.ErroAPI as erro:
    st.error(str(erro))
    st.stop()
st.session_state["minuta"] = minuta

id_minuta = minuta["id_minuta"]
aprovada = bool(minuta.get("aprovada"))
exportado = minuta.get("situacao") == "exportado"
conteudo = minuta["conteudo"]
fontes = {f["marcador"]: f["referencia"] for f in minuta["fontes"]}

st.progress(3 / 3, text="Etapa 3 de 3 — Confirmação e validação")
st.title("Confirmação dos campos e validação do DFD")
st.caption(f"{minuta.get('numero') or ''} · {minuta.get('titulo') or ''} · versão {minuta['versao']}")

analise = minuta.get("analise") or {}
if analise:
    cliente.painel_analise(analise, compacto=True)
    if not analise.get("pronto_para_validacao") and not aprovada:
        st.warning(
            "A análise automática aponta alertas nesta versão. Você pode aprovar sob sua "
            "responsabilidade, mas considere voltar à revisão.",
            icon=":material/rule:",
        )

situacao_por_campo = {c["campo"]: c for c in analise.get("checklist", [])}
confirmacao, decisao = st.columns([1.4, 1], gap="large")

# --------------------------------------------------- confirmação campo a campo
with confirmacao:
    st.subheader("Confirme um a um os campos obrigatórios")
    st.caption("Art. 8º do Decreto nº 10.947/2022")

    confirmados: list[str] = []
    for chave, rotulo in CAMPOS_ART_8.items():
        if chave == "area_requisitante":
            valor = f"{minuta.get('unidade_requisitante') or '—'} — {minuta.get('responsavel') or '—'}"
        else:
            campo = conteudo.get(chave) or {}
            valor = (campo.get("texto") or campo.get("valor") or "").strip()

        with st.container(border=True):
            # Chave por versao: confirmacoes de outro DFD/versao nao sao herdadas.
            marcado = st.checkbox(
                rotulo, key=f"conf_{chave}_{id_minuta}", value=aprovada, disabled=aprovada
            )
            if marcado:
                confirmados.append(chave)
            if valor:
                st.caption(valor[:300] + ("…" if len(valor) > 300 else ""))
            else:
                st.caption(":orange[Campo sem conteúdo — confirme apenas se não se aplica.]")

            if fundamentos := (conteudo.get(chave) or {}).get("fundamentos"):
                st.caption("Fonte: " + "; ".join(fontes.get(f, f) for f in fundamentos))
            if (item := situacao_por_campo.get(chave)) and item["situacao"] != "ok":
                st.caption(f":{cliente.COR_SITUACAO[item['situacao']]}[{item['mensagem']}]")

    total = len(CAMPOS_ART_8)
    st.progress(len(confirmados) / total, text=f"{len(confirmados)} de {total} confirmados")

# --------------------------------------------------- validação
with decisao:
    st.subheader("Validação do agente público")
    with st.container(border=True):
        st.text_input("Responsável pela decisão", value=usuario["nome"], disabled=True)
        st.text_input("Versão avaliada", value=f"v{minuta['versao']}", disabled=True)

        if aprovada:
            st.success("Esta versão já foi aprovada.", icon=":material/task_alt:")
        elif exportado:
            st.info("DFD exportado.")
        else:
            st.markdown(
                "Declaro que revisei a minuta e os fundamentos normativos recuperados, "
                "e aprovo o documento sob minha responsabilidade."
            )
            declarou = st.checkbox("Li e concordo", key=f"declaracao_{id_minuta}")
            observacoes = st.text_area(
                "Observações da validação",
                height=90,
                key=f"obs_{id_minuta}",
                help="Obrigatório ao devolver: explique o que precisa ser ajustado.",
            )

            pode_aprovar = len(confirmados) == total and declarou
            if not pode_aprovar:
                st.caption(
                    ":gray[Confirme todos os campos e marque a declaração para habilitar a aprovação.]"
                )

            if st.button(
                "Aprovar DFD", type="primary", disabled=not pode_aprovar, width="stretch"
            ):
                try:
                    cliente.validar(id_dfd, id_minuta, "aprovado", confirmados, observacoes)
                except cliente.ErroAPI as erro:
                    st.error(str(erro))
                    st.stop()
                cliente.avisar_depois("DFD aprovado. A exportação está liberada.")
                st.rerun()

            if st.button("Devolver para ajustes", width="stretch"):
                if not observacoes.strip():
                    st.error("Informe nas observações o motivo da devolução.")
                    st.stop()
                try:
                    cliente.validar(id_dfd, id_minuta, "devolvido", confirmados, observacoes)
                except cliente.ErroAPI as erro:
                    st.error(str(erro))
                    st.stop()
                cliente.avisar_depois("Documento devolvido para ajustes.", ":material/undo:")
                st.session_state.pop("minuta", None)
                st.switch_page("pages/2_Revisar_minuta.py")

    st.caption("A exportação permanece bloqueada até o registro da validação humana (RN01).")

# --------------------------------------------------- exportação
st.divider()
st.subheader("Exportação")

if aprovada:
    if st.button("Exportar DFD em .docx", type="primary"):
        try:
            resultado = cliente.exportar(id_dfd)
        except cliente.ErroAPI as erro:
            st.error(str(erro), icon=":material/lock:")
            st.stop()
        cliente.avisar_depois(f"Documento gerado: {resultado['arquivo']}", ":material/description:")
        st.rerun()
elif not exportado:
    st.button("Exportar DFD em .docx", disabled=True,
              help="Disponível após a aprovação da versão atual.")

try:
    arquivos = cliente.exportacoes(id_dfd)
except cliente.ErroAPI as erro:
    st.error(str(erro))
    arquivos = []

for exp in arquivos:
    with st.container(border=True):
        info, acao = st.columns([4, 1.3])
        info.markdown(
            f"**Exportação {exp['id_exportacao']}** · versão {exp['versao']} · "
            f"{exp['data_hora'][:16].replace('T', ' ')} UTC"
        )
        info.caption(f"SHA-256: {exp['hash_arquivo']}")
        chave = f"arquivo_{exp['id_exportacao']}"
        if chave not in st.session_state:
            if acao.button("Preparar download", key=f"prep_{exp['id_exportacao']}",
                           width="stretch"):
                try:
                    st.session_state[chave] = cliente.baixar(exp["url_download"])
                except cliente.ErroAPI as erro:
                    st.error(str(erro))
                    st.stop()
                st.rerun()
        else:
            acao.download_button(
                "Baixar .docx",
                data=st.session_state[chave],
                file_name=f"{(minuta.get('numero') or f'DFD_{id_dfd}').replace('/', '-').replace(' ', '_')}"
                f"_v{exp['versao']}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                width="stretch",
                key=f"baixar_{exp['id_exportacao']}",
            )
