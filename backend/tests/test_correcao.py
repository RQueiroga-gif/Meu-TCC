"""Revisao de redacao por um segundo modelo: sugestao, salvaguardas e fluxo HTTP.

O revisor e simulado: nenhum teste usa rede nem chave real.
"""
from __future__ import annotations

import json

import pytest

from app.llm.base import ErroProvedorLLM, ProvedorLLM, Resposta
from app.services import correcao

CONTEUDO = {
    "justificativa_necessidade": {
        "texto": "A aquizição se justifica pela falta de monitores no setor, "
                 "conforme o art. 18 da Lei nº 14.133/2021.",
        "fundamentos": ["T1", "T2"],
    },
    "descricao_objeto": {"texto": "Aquisição de 6 monitores pro setor de suporte.",
                         "fundamentos": ["T1"]},
    "quantidade": {"valor": "6 monitores", "evidencia": "6 monitores", "origem": "modelo"},
    "estimativa_valor": {"valor": None, "evidencia": None, "origem": None},
}
CORRIGIDO_I = ("A aquisição justifica-se pela falta de monitores no setor, "
               "conforme o art. 18 da Lei nº 14.133/2021.")
CORRIGIDO_II = "Aquisição de 6 monitores para o setor de suporte."


class RevisorFalso(ProvedorLLM):
    """Devolve a resposta fixada no teste e guarda o prompt recebido."""

    nome = "falso"

    def __init__(self, campos: dict | None = None, erro: Exception | None = None) -> None:
        self.campos = campos or {}
        self.erro = erro
        self.prompts: list[str] = []

    def completar(self, prompt_sistema, prompt_usuario, *, formato_json=True):  # noqa: ANN001
        self.prompts.append(prompt_usuario)
        if self.erro:
            raise self.erro
        corpo = {"campos": self.campos, "observacoes": ["Corrigida a grafia de “aquisição”."]}
        return Resposta(json.dumps(corpo, ensure_ascii=False), "revisor-1", {"provedor": "falso"})


@pytest.fixture
def revisor(monkeypatch):
    def instalar(**kwargs) -> RevisorFalso:
        falso = RevisorFalso(**kwargs)
        monkeypatch.setattr(correcao, "provedor_correcao", lambda: falso)
        return falso
    return instalar


# ------------------------------------------------------------------ servico
def test_sugere_correcao_e_preserva_o_resto(revisor):
    falso = revisor(campos={"justificativa_necessidade": CORRIGIDO_I,
                            "descricao_objeto": CORRIGIDO_II})
    sugestao = correcao.sugerir(CONTEUDO)

    assert [c.campo for c in sugestao.campos if c.alterado] == [
        "justificativa_necessidade", "descricao_objeto"
    ]
    assert sugestao.modelo == "revisor-1"
    assert sugestao.parametros["versao_prompt"] == correcao.VERSAO
    # so os campos redigidos vao ao revisor; valores extraidos nao saem do sistema
    enviado = json.loads(falso.prompts[0].split("CAMPOS:\n", 1)[1].split("\n\nFORMATO", 1)[0])
    assert set(enviado) == {"justificativa_necessidade", "descricao_objeto"}

    novo = sugestao.aplicar(CONTEUDO)
    assert novo["justificativa_necessidade"] == {"texto": CORRIGIDO_I, "fundamentos": ["T1", "T2"]}
    assert novo["descricao_objeto"]["fundamentos"] == ["T1"]
    assert novo["quantidade"] == CONTEUDO["quantidade"]
    assert CONTEUDO["descricao_objeto"]["texto"].endswith("pro setor de suporte.")  # nao muta


@pytest.mark.parametrize(
    ("sugerido", "motivo"),
    [
        # acrescenta um artigo que nao estava no texto
        (CORRIGIDO_I.replace("art. 18", "arts. 18 e 72"), "artigos ou normas"),
        # troca a norma citada
        (CORRIGIDO_I.replace("Lei nº 14.133/2021", "Decreto nº 10.947/2022"), "artigos ou normas"),
        # remove a mencao
        ("A aquisição justifica-se pela falta de monitores no setor.", "artigos ou normas"),
        # incha o texto
        (CORRIGIDO_I + " Além disso, " + "o setor precisa de mais equipamentos. " * 12,
         "acrescentava"),
        # muda a quantidade (fato da demanda, nao redacao)
        (CORRIGIDO_I.replace("falta de monitores", "falta de 8 monitores"), "números"),
    ],
)
def test_salvaguardas_descartam_a_sugestao(revisor, sugerido, motivo):
    revisor(campos={"justificativa_necessidade": sugerido})
    campo = correcao.sugerir(CONTEUDO).campos[0]
    assert not campo.alterado
    assert campo.sugerido == campo.original
    assert motivo in campo.motivo_descarte


def test_campo_ausente_na_resposta_fica_como_esta(revisor):
    revisor(campos={"descricao_objeto": CORRIGIDO_II})
    campos = {c.campo: c for c in correcao.sugerir(CONTEUDO).campos}
    assert not campos["justificativa_necessidade"].alterado
    assert campos["descricao_objeto"].sugerido == CORRIGIDO_II


def test_dados_pessoais_sao_minimizados_antes_e_depois(revisor):
    falso = revisor(campos={"descricao_objeto": "Contato: fulano@orgao.gov.br."})
    conteudo = {**CONTEUDO, "descricao_objeto": {
        "texto": "Aquisição; CPF 123.456.789-09.", "fundamentos": []}}
    sugestao = correcao.sugerir(conteudo)
    assert "123.456.789-09" not in falso.prompts[0]
    campo = {c.campo: c for c in sugestao.campos}["descricao_objeto"]
    assert "fulano@orgao.gov.br" not in campo.sugerido


def test_revisor_usa_provedor_e_modelo_proprios(monkeypatch):
    monkeypatch.setattr(correcao.config, "LLM_PROVEDOR_CORRECAO", "gemini")
    monkeypatch.setattr(correcao.config, "LLM_MODELO_CORRECAO", "gemini-lite-teste")
    monkeypatch.setattr(correcao.config, "GEMINI_API_KEY", "chave-falsa")
    provedor = correcao.provedor_correcao()
    assert provedor.nome == "gemini" and provedor._modelo == "gemini-lite-teste"
    # o gerador continua o configurado em LLM_PROVEDOR (echo nos testes)
    from app.llm.factory import obter_provedor
    assert obter_provedor().nome == "echo"


def test_echo_nao_revisa(monkeypatch):
    monkeypatch.setattr(correcao.config, "LLM_PROVEDOR_CORRECAO", "echo")
    sugestao = correcao.sugerir(CONTEUDO)
    assert sugestao.provedor == "echo"
    assert not any(c.alterado for c in sugestao.campos)


# ------------------------------------------------------------------ rotas
DEMANDA = {
    "unidade_requisitante": "Coordenadoria de Protocolo",
    "titulo": "Aquisição de equipamentos de informática",
    "descricao": (
        "Necessidade de aquisição de 40 notebooks para os laboratórios das escolas, "
        "com prioridade alta, destinados à formalização da demanda e ao planejamento "
        "da contratação no plano de contratações anual."
    ),
}


def _dfd_com_minuta(cliente, cabecalho) -> tuple[int, dict]:  # noqa: ANN001
    id_dfd = cliente.post("/demandas", json=DEMANDA, headers=cabecalho).json()["id_dfd"]
    minuta = cliente.post(f"/dfd/{id_dfd}/gerar", json={}, headers=cabecalho).json()
    return id_dfd, minuta


def test_rota_sugere_sem_gravar_e_aplicacao_vira_versao(cliente, cabecalho, revisor):  # noqa: ANN001
    id_dfd, minuta = _dfd_com_minuta(cliente, cabecalho)
    texto = minuta["conteudo"]["descricao_objeto"]["texto"]
    revisor(campos={"descricao_objeto": texto.replace("sucinta", "concisa")})

    resposta = cliente.post(f"/dfd/{id_dfd}/corrigir", json={}, headers=cabecalho)
    assert resposta.status_code == 200, resposta.text
    sugestao = resposta.json()
    alterados = [c["campo"] for c in sugestao["campos"] if c["alterado"]]
    assert alterados == ["descricao_objeto"]
    assert "concisa" in sugestao["conteudo_sugerido"]["descricao_objeto"]["texto"]

    # sugerir nao cria versao
    assert len(cliente.get(f"/dfd/{id_dfd}/versoes", headers=cabecalho).json()) == 1

    # o agente aceita: a sugestao vira versao pelo fluxo de revisao
    resposta = cliente.post(
        f"/dfd/{id_dfd}/revisar",
        json={"conteudo": sugestao["conteudo_sugerido"],
              "correcao_ia": {"provedor": sugestao["provedor"], "modelo": sugestao["modelo"]}},
        headers=cabecalho,
    )
    assert resposta.status_code == 200, resposta.text
    nova = resposta.json()
    assert nova["versao"] == 2 and nova["origem"] == "edicao"
    assert nova["conteudo"]["descricao_objeto"]["fundamentos"] == (
        minuta["conteudo"]["descricao_objeto"]["fundamentos"]
    )

    eventos = cliente.get(f"/auditoria?id_dfd={id_dfd}", headers=cabecalho).json()
    sugerida = next(e for e in eventos if e["acao"] == "CORRECAO_SUGERIDA")
    assert sugerida["detalhe"]["alterados"] == ["descricao_objeto"]
    edicao = next(e for e in eventos if e["acao"] == "EDICAO")
    assert edicao["detalhe"]["correcao_ia"] == {"provedor": "falso", "modelo": "revisor-1"}


def test_rota_revisa_o_texto_em_edicao(cliente, cabecalho, revisor):  # noqa: ANN001
    id_dfd, minuta = _dfd_com_minuta(cliente, cabecalho)
    falso = revisor(campos={})
    em_edicao = {**minuta["conteudo"],
                 "descricao_objeto": {"texto": "Texto ainda nao salvo.", "fundamentos": ["T1"]}}
    resposta = cliente.post(f"/dfd/{id_dfd}/corrigir", json={"conteudo": em_edicao},
                            headers=cabecalho)
    assert resposta.status_code == 200, resposta.text
    assert "Texto ainda nao salvo." in falso.prompts[0]


def test_falha_do_revisor_preserva_a_minuta(cliente, cabecalho, revisor):  # noqa: ANN001
    id_dfd, _ = _dfd_com_minuta(cliente, cabecalho)
    revisor(erro=ErroProvedorLLM("cota esgotada"))

    resposta = cliente.post(f"/dfd/{id_dfd}/corrigir", json={}, headers=cabecalho)
    assert resposta.status_code == 503
    assert "não foi alterada" in resposta.json()["detail"]
    assert len(cliente.get(f"/dfd/{id_dfd}/versoes", headers=cabecalho).json()) == 1
    eventos = cliente.get(f"/auditoria?id_dfd={id_dfd}", headers=cabecalho).json()
    assert "FALHA_CORRECAO" in {e["acao"] for e in eventos}


def test_rota_de_outro_usuario_e_negada(cliente, cabecalho, cabecalho_outro, revisor):  # noqa: ANN001
    id_dfd, _ = _dfd_com_minuta(cliente, cabecalho)
    revisor(campos={})
    resposta = cliente.post(f"/dfd/{id_dfd}/corrigir", json={}, headers=cabecalho_outro)
    assert resposta.status_code in (403, 404)
