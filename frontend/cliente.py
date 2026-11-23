"""Cliente HTTP do backend — unico ponto de contato da interface com a API.

A camada de apresentacao conhece apenas o contrato HTTP: nao acessa banco, indice
vetorial nem provedor de modelo de linguagem. Trocar a interface por outra
tecnologia nao altera nenhuma regra de negocio.
"""
from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st

BASE = os.getenv("API_URL", "http://localhost:8001").rstrip("/")
TIMEOUT = int(os.getenv("API_TIMEOUT", "180"))

ROTULOS_ART_8 = {
    "justificativa_necessidade": "I — Justificativa da necessidade",
    "descricao_objeto": "II — Descrição sucinta do objeto",
    "quantidade": "III — Quantidade a ser contratada",
    "estimativa_valor": "IV — Estimativa preliminar do valor",
    "data_pretendida": "V — Data pretendida para conclusão",
    "grau_prioridade": "VI — Grau de prioridade",
    "vinculacao_outro_dfd": "VII — Vinculação com outro DFD",
    "area_requisitante": "VIII — Área requisitante e responsável",
}
REDIGIDOS = ("justificativa_necessidade", "descricao_objeto")
ICONE_SITUACAO = {"ok": ":material/check_circle:", "alerta": ":material/warning:",
                  "pendente": ":material/pending:"}
COR_SITUACAO = {"ok": "green", "alerta": "orange", "pendente": "gray"}


class ErroAPI(RuntimeError):
    """Erro tratado da API, com mensagem pronta para o usuário."""

    def __init__(self, mensagem: str, codigo: int) -> None:
        super().__init__(mensagem)
        self.codigo = codigo


def _cabecalho() -> dict[str, str]:
    token = st.session_state.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _detalhe(resposta: requests.Response) -> str:
    try:
        detalhe = resposta.json().get("detail", "")
    except ValueError:
        return resposta.text[:300]
    if isinstance(detalhe, list):     # erro de validacao do FastAPI
        return "; ".join(str(d.get("msg", d)) for d in detalhe)
    return str(detalhe)


def _requisitar(metodo: str, rota: str, **kwargs) -> requests.Response:
    try:
        resposta = requests.request(
            metodo, f"{BASE}{rota}", headers=_cabecalho(), timeout=TIMEOUT, **kwargs
        )
    except requests.Timeout as erro:
        raise ErroAPI("O serviço demorou a responder. Tente novamente.", 504) from erro
    except requests.ConnectionError as erro:
        raise ErroAPI(
            f"Não foi possível falar com a API em {BASE}. "
            "Verifique se o backend está em execução.",
            503,
        ) from erro

    if resposta.status_code < 400:
        return resposta
    detalhe = _detalhe(resposta)
    if resposta.status_code == 401 and rota != "/auth/login":
        st.session_state.pop("token", None)
        raise ErroAPI("Sessão expirada. Entre novamente.", 401)
    if resposta.status_code == 422 and not detalhe:
        detalhe = "Dados inválidos no formulário."
    raise ErroAPI(detalhe or f"Erro {resposta.status_code}.", resposta.status_code)


def _chamar(metodo: str, rota: str, **kwargs) -> Any:
    resposta = _requisitar(metodo, rota, **kwargs)
    return resposta.json() if resposta.content else None


# ------------------------------------------------------------------ chamadas
def entrar(email: str, senha: str) -> dict[str, Any]:
    return _chamar("POST", "/auth/login", json={"email": email, "senha": senha})


def listar_dfds() -> list[dict[str, Any]]:
    return _chamar("GET", "/demandas") or []


def registrar_demanda(unidade: str, titulo: str, descricao: str) -> dict[str, Any]:
    return _chamar(
        "POST",
        "/demandas",
        json={"unidade_requisitante": unidade, "titulo": titulo, "descricao": descricao},
    )


def obter_demanda(id_dfd: int) -> dict[str, Any]:
    return _chamar("GET", f"/demandas/{id_dfd}")


def ajustar_demanda(id_dfd: int, unidade: str, titulo: str, descricao: str) -> dict[str, Any]:
    return _chamar(
        "PUT",
        f"/demandas/{id_dfd}",
        json={"unidade_requisitante": unidade, "titulo": titulo, "descricao": descricao},
    )


def gerar_minuta(id_dfd: int, **parametros) -> dict[str, Any]:
    return _chamar("POST", f"/dfd/{id_dfd}/gerar", json=parametros)


def obter_minuta(id_dfd: int) -> dict[str, Any]:
    return _chamar("GET", f"/dfd/{id_dfd}")


def revisar_minuta(
    id_dfd: int, conteudo: dict[str, Any], correcao_ia: dict[str, str] | None = None
) -> dict[str, Any]:
    corpo: dict[str, Any] = {"conteudo": conteudo}
    if correcao_ia:
        corpo["correcao_ia"] = correcao_ia
    return _chamar("POST", f"/dfd/{id_dfd}/revisar", json=corpo)


def corrigir_minuta(id_dfd: int, conteudo: dict[str, Any]) -> dict[str, Any]:
    """Sugestão de redação para os campos I e II; nada é gravado."""
    return _chamar("POST", f"/dfd/{id_dfd}/corrigir", json={"conteudo": conteudo})


def validar(
    id_dfd: int, id_minuta: int, decisao: str, campos: list[str], justificativa: str | None = None
) -> dict[str, Any]:
    return _chamar(
        "POST",
        f"/dfd/{id_dfd}/validar",
        json={
            "id_minuta": id_minuta,
            "decisao": decisao,
            "campos_confirmados": campos,
            "justificativa": justificativa or None,
        },
    )


def exportar(id_dfd: int) -> dict[str, Any]:
    return _chamar("POST", f"/dfd/{id_dfd}/exportar")


def exportacoes(id_dfd: int) -> list[dict[str, Any]]:
    return _chamar("GET", f"/dfd/{id_dfd}/exportacoes") or []


def baixar(url_download: str) -> bytes:
    """Conteudo binario do .docx exportado."""
    return _requisitar("GET", url_download).content


def versoes(id_dfd: int) -> list[dict[str, Any]]:
    return _chamar("GET", f"/dfd/{id_dfd}/versoes") or []


def estatisticas_base() -> dict[str, Any]:
    return _chamar("GET", "/normas/estatisticas")


def listar_normas() -> list[dict[str, Any]]:
    return _chamar("GET", "/normas") or []


def alterar_vigencia(id_norma: int, vigente: bool) -> dict[str, Any]:
    return _chamar("POST", f"/normas/{id_norma}/vigencia", params={"vigente": str(vigente).lower()})


def reindexar_base() -> dict[str, Any]:
    return _chamar("POST", "/normas/reindexar")


def buscar_normas(consulta: str, k: int = 6) -> dict[str, Any]:
    return _chamar("GET", "/normas/buscar", params={"consulta": consulta, "k": k})


def auditoria(id_dfd: int | None = None, limite: int = 100, acao: str | None = None) -> list[dict[str, Any]]:
    parametros: dict[str, Any] = {"limite": limite}
    if id_dfd:
        parametros["id_dfd"] = id_dfd
    if acao:
        parametros["acao"] = acao
    return _chamar("GET", "/auditoria", params=parametros) or []


def integridade_auditoria() -> dict[str, Any]:
    return _chamar("GET", "/auditoria/integridade")


def indicadores() -> dict[str, Any]:
    return _chamar("GET", "/indicadores")


# ------------------------------------------------------------------ utilidades
def exigir_sessao() -> dict[str, Any]:
    """Interrompe a página se não houver usuário autenticado."""
    if not st.session_state.get("token"):
        st.warning("Entre no sistema para continuar.")
        st.page_link("app.py", label="Ir para o acesso", icon=":material/login:")
        st.stop()
    return st.session_state["usuario"]


def exigir_dfd() -> int:
    """Interrompe a página se nenhum DFD estiver selecionado."""
    if not st.session_state.get("id_dfd"):
        st.info("Selecione ou registre uma demanda antes de continuar.")
        st.page_link(
            "pages/1_Registrar_demanda.py", label="Registrar demanda", icon=":material/edit:"
        )
        st.stop()
    return st.session_state["id_dfd"]


def avisar_depois(mensagem: str, icone: str = ":material/check:") -> None:
    """Guarda uma mensagem para exibir apos st.rerun()/st.switch_page()."""
    st.session_state["_aviso"] = (mensagem, icone)


def exibir_aviso_pendente() -> None:
    if aviso := st.session_state.pop("_aviso", None):
        st.toast(aviso[0], icon=aviso[1])


def painel_analise(analise: dict[str, Any], *, compacto: bool = False) -> None:
    """Checklist do art. 8º e indicadores de rastreabilidade (analise do backend)."""
    if not analise:
        return
    resumo = analise.get("resumo", {})
    indicadores_ = analise.get("indicadores", {})
    # v0.4: grade 2 x 2 (em quatro colunas o painel lateral truncava os valores)
    linha_1, linha_2 = st.columns(2), st.columns(2)
    linha_1[0].metric("Prontidão", f"{analise.get('prontidao', 0):.0%}")
    taxa = indicadores_.get("taxa_ancoragem")
    linha_1[1].metric("Ancoragem", f"{taxa:.0%}" if taxa is not None else "—")
    linha_2[0].metric("Alertas", resumo.get("alerta", 0))
    linha_2[1].metric("Pendências", resumo.get("pendente", 0))
    if compacto:
        return
    for item in analise.get("checklist", []):
        situacao = item["situacao"]
        st.markdown(
            f":{COR_SITUACAO.get(situacao, 'gray')}[{ICONE_SITUACAO.get(situacao, '')}] "
            f"**{ROTULOS_ART_8.get(item['campo'], item['campo'])}** — {item['mensagem']}"
        )
    normas = indicadores_.get("normas_citadas") or []
    st.caption(
        f"{indicadores_.get('fontes_citadas', 0)} de {indicadores_.get('fontes_recuperadas', 0)} "
        f"fontes citadas" + (f" · normas: {', '.join(normas)}" if normas else "")
    )
