"""Tela 2 — Revisão da minuta com o painel de fontes (UC05, RF05, RF06, RNF02).

A decisão de interface mais importante do protótipo: o texto gerado à esquerda e
os fundamentos normativos que o sustentam à direita, visíveis durante toda a
revisão. A rastreabilidade não fica escondida em um relatório posterior.
"""
from __future__ import annotations

import streamlit as st

import cliente

st.set_page_config(page_title="Revisar minuta", layout="wide")
cliente.exigir_sessao()
id_dfd = cliente.exigir_dfd()
cliente.exibir_aviso_pendente()

ROTULOS = {k: v for k, v in cliente.ROTULOS_ART_8.items() if k != "area_requisitante"}

# Sem cache global: st.cache_data e compartilhado entre TODOS os usuarios do
# servidor, o que exporia a minuta de um agente a outro sem checagem de acesso.
minuta = st.session_state.get("minuta")
if not minuta or minuta.get("id_dfd") != id_dfd:
    try:
        minuta = cliente.obter_minuta(id_dfd)
    except cliente.ErroAPI as erro:
        if erro.codigo == 404:
            st.info("Este DFD ainda não tem minuta. Ajuste a demanda e gere a minuta.")
            if st.button("Ir para a demanda", type="primary"):
                st.session_state["modo_ajuste"] = True
                st.switch_page("pages/1_Registrar_demanda.py")
        else:
            st.error(str(erro))
        st.stop()
    st.session_state["minuta"] = minuta

id_minuta = minuta["id_minuta"]
exportado = minuta.get("situacao") == "exportado"

st.progress(2 / 3, text="Etapa 2 de 3 — Revisão da minuta")
st.title("Revisão da minuta preliminar do DFD")
ORIGEM_VERSAO = {"geracao": "gerada pelo modelo", "edicao": "editada pelo agente"}
st.caption(
    f"{minuta.get('numero') or ''} · {minuta.get('titulo') or ''} · "
    f"versão {minuta['versao']} ({ORIGEM_VERSAO.get(minuta['origem'], minuta['origem'])})"
)
if exportado:
    st.info("Este DFD já foi exportado: a minuta é exibida apenas para consulta.",
            icon=":material/lock:")

verificacao = minuta.get("verificacao") or {}
if verificacao.get("inconsistencia_detectada"):
    st.warning(
        "A verificação automática encontrou inconsistências entre o texto e os "
        "fundamentos recuperados. Confira os campos marcados antes de prosseguir.",
        icon=":material/report:",
    )
elif verificacao.get("taxa_ancoragem") is not None:
    # v0.4: o selo diz o que a verificacao confere e o que nao confere, para
    # nao ser lido como "o documento esta correto" (calibracao da confianca).
    st.success(
        f"Verificação automática: as citações de {verificacao['taxa_ancoragem']:.0%} dos "
        "campos redigidos conferem com os trechos recuperados. A interpretação jurídica "
        "não é verificada: leia o texto e as fontes antes de aprovar.",
        icon=":material/verified:",
    )

editor, lateral = st.columns([1.6, 1], gap="large")

# ------------------------------------------------------------------ editor
with editor:
    st.subheader("Minuta gerada")
    conteudo = {k: dict(v) if isinstance(v, dict) else v for k, v in minuta["conteudo"].items()}
    problemas = verificacao.get("por_campo", {})
    marcadores = {f["marcador"]: f["referencia"] for f in minuta["fontes"]}

    for chave, rotulo in ROTULOS.items():
        campo = conteudo.get(chave)
        if not isinstance(campo, dict):
            campo = {"texto": ""} if chave in cliente.REDIGIDOS else {"valor": None}
        chave_widget = f"{chave}_{id_minuta}"   # muda a cada versao: sem texto velho

        if chave in cliente.REDIGIDOS:
            texto = st.text_area(rotulo, value=campo.get("texto") or "", height=180,
                                 key=chave_widget, disabled=exportado)
            conteudo[chave] = {**campo, "texto": texto}
            if fundamentos := campo.get("fundamentos"):
                st.caption("Fundamentos: " + "; ".join(
                    f"{f} — {marcadores.get(f, 'fora do contexto')}" for f in fundamentos
                ))
            problema = problemas.get(chave, {})
            if problema.get("fundamentos_invalidos"):
                st.error(
                    "Fundamento citado que não existe no contexto: "
                    + ", ".join(problema["fundamentos_invalidos"]),
                    icon=":material/link_off:",
                )
            if problema.get("artigos_fora_do_contexto"):
                st.error(
                    "Artigo mencionado fora dos trechos recuperados: art. "
                    + ", art. ".join(problema["artigos_fora_do_contexto"]),
                    icon=":material/gavel:",
                )
        else:
            valor = st.text_input(rotulo, value=campo.get("valor") or "", key=chave_widget,
                                  disabled=exportado)
            conteudo[chave] = {**campo, "valor": valor.strip() or None}
            if campo.get("origem") == "agente" and campo.get("valor"):
                st.caption("Informado pelo agente público.")
            elif campo.get("evidencia"):
                st.caption(f"Extraído da demanda: “{campo['evidencia']}”")
                if problemas.get(chave, {}).get("extracao_sem_evidencia"):
                    st.error("A evidência não aparece na demanda: confira o valor.",
                             icon=":material/help:")
            elif not campo.get("valor"):
                st.caption("Não informado na demanda — preencha se aplicável.")

    if minuta.get("pendencias"):
        st.info(
            "Campos sem informação na demanda: "
            + ", ".join(ROTULOS.get(p, p) for p in minuta["pendencias"])
            + ". O sistema não estima valores, quantidades ou datas.",
            icon=":material/pending:",
        )

# ------------------------------------------------------------------ lateral
with lateral:
    aba_fontes, aba_analise, aba_versoes = st.tabs(["Fontes", "Conformidade", "Versões"])

    with aba_fontes:
        if not minuta["fontes"]:
            st.caption("Nenhum trecho vinculado a esta versão.")
        citados = {
            f for c in cliente.REDIGIDOS for f in (minuta["conteudo"].get(c) or {}).get("fundamentos", [])
        }
        for fonte in minuta["fontes"]:
            with st.container(border=True):
                selo = " · :green[citado]" if fonte["marcador"] in citados else ""
                st.markdown(f"**[{fonte['marcador']}] {fonte['referencia']}**{selo}")
                st.progress(
                    min(max(fonte["similaridade"], 0.0), 1.0),
                    text=f"similaridade {fonte['similaridade']:.2f}".replace(".", ","),
                )
                with st.expander("Ver trecho completo"):
                    st.write(fonte["texto"])

    with aba_analise:
        st.caption("Análise do art. 8º do Decreto nº 10.947/2022 (versão exibida).")
        cliente.painel_analise(minuta.get("analise") or {})

    with aba_versoes:
        st.caption(
            f"Versão exibida: modelo {minuta.get('modelo_llm') or '—'} · "
            f"prompt {minuta.get('versao_prompt') or '—'}"
        )
        try:
            historico = cliente.versoes(id_dfd)
        except cliente.ErroAPI as erro:
            st.error(str(erro))
            historico = []
        for versao in reversed(historico):
            decisao = {"aprovado": " · :green[aprovada]", "devolvido": " · :orange[devolvida]"}.get(
                versao.get("decisao") or "", ""
            )
            st.caption(
                f"v{versao['versao']} · {ORIGEM_VERSAO.get(versao['origem'], versao['origem'])} · "
                f"{versao.get('autor') or '—'} · "
                f"{versao['data_geracao'][:16].replace('T', ' ')} UTC{decisao}"
            )

# ------------------------------------------------------------------ ações
st.divider()
if exportado:
    if st.button("Ver validação e arquivos exportados", type="primary"):
        st.switch_page("pages/3_Validar_e_exportar.py")
    st.stop()

alterado = conteudo != minuta["conteudo"]

# ------------------------------------------------------------ revisão de texto
# A sugestão do revisor nunca vira versão sozinha: o agente compara e decide.
sugestao = st.session_state.get("correcao")
if sugestao and sugestao.get("id_minuta") == id_minuta:
    with st.container(border=True):
        st.subheader("Sugestão de revisão de texto")
        st.caption(
            f"Revisor: {sugestao['provedor']} · {sugestao['modelo'] or '—'}. "
            "Só a redação dos campos I e II é revisada; valores, datas e fundamentos não mudam."
        )
        mudancas = [c for c in sugestao["campos"] if c["alterado"]]
        for campo in sugestao["campos"]:
            if campo.get("motivo_descarte"):
                st.warning(
                    f"{ROTULOS.get(campo['campo'], campo['campo'])}: sugestão descartada "
                    f"({campo['motivo_descarte']}).",
                    icon=":material/shield:",
                )
        for campo in mudancas:
            st.markdown(f"**{ROTULOS.get(campo['campo'], campo['campo'])}**")
            antes, depois = st.columns(2)
            # text_area somente leitura: texto puro, sem interpretar "R$" como fórmula
            chave = f"corr_{campo['campo']}_{hash((campo['original'], campo['sugerido']))}"
            antes.text_area("Texto atual", campo["original"], height=160, disabled=True,
                            key=f"{chave}_antes")
            depois.text_area("Texto sugerido", campo["sugerido"], height=160, disabled=True,
                             key=f"{chave}_depois")
        if sugestao.get("observacoes"):
            with st.expander("O que o revisor alterou"):
                for obs in sugestao["observacoes"]:
                    st.markdown("- " + obs.replace("$", "\\$"))

        col_aplicar, col_descartar = st.columns(2)
        if not mudancas:
            st.success("Nenhuma correção sugerida para o texto atual.", icon=":material/check:")
        elif col_aplicar.button("Aplicar sugestão", type="primary", width="stretch",
                                help="Grava os textos sugeridos como nova versão da minuta."):
            try:
                st.session_state["minuta"] = cliente.revisar_minuta(
                    id_dfd,
                    sugestao["conteudo_sugerido"],
                    correcao_ia={"provedor": sugestao["provedor"], "modelo": sugestao["modelo"]},
                )
            except cliente.ErroAPI as erro:
                st.error(str(erro))
                st.stop()
            st.session_state.pop("correcao", None)
            cliente.avisar_depois("Revisão de texto aplicada como nova versão.")
            st.rerun()
        if col_descartar.button("Descartar sugestão", width="stretch"):
            st.session_state.pop("correcao", None)
            st.rerun()

col_voltar, col_regerar, col_corrigir, col_salvar, col_seguir = st.columns(5)


def _gravar() -> bool:
    """Grava a revisão, se houver alteração. Devolve True se deu certo."""
    if not alterado:
        return True
    try:
        st.session_state["minuta"] = cliente.revisar_minuta(id_dfd, conteudo)
    except cliente.ErroAPI as erro:
        st.error(str(erro))
        return False
    return True


if col_voltar.button("Ajustar demanda", width="stretch"):
    st.session_state["modo_ajuste"] = True
    st.switch_page("pages/1_Registrar_demanda.py")

if col_regerar.button("Solicitar nova geração", width="stretch",
                      help="Gera uma nova versão a partir da demanda; edições não salvas se perdem."):
    with st.spinner("Gerando nova versão…"):
        try:
            st.session_state["minuta"] = cliente.gerar_minuta(id_dfd)
        except cliente.ErroAPI as erro:
            st.error(str(erro))
            st.stop()
    cliente.avisar_depois("Nova versão gerada.")
    st.rerun()

if col_corrigir.button("Revisar texto", width="stretch", icon=":material/spellcheck:",
                       help="Um segundo modelo sugere correções de redação nos campos I e II. "
                            "Nada é gravado até você aplicar."):
    with st.spinner("Revisando o texto…"):
        try:
            resultado = cliente.corrigir_minuta(id_dfd, conteudo)
        except cliente.ErroAPI as erro:
            st.error(str(erro))
            st.stop()
    st.session_state["correcao"] = {**resultado, "id_minuta": id_minuta}
    st.rerun()

if col_salvar.button("Salvar revisão", width="stretch", disabled=not alterado):
    if _gravar():
        cliente.avisar_depois("Revisão gravada como nova versão.")
        st.rerun()

if col_seguir.button("Prosseguir para validação", type="primary", width="stretch"):
    if _gravar():
        st.switch_page("pages/3_Validar_e_exportar.py")
