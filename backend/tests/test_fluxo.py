"""Teste de ponta a ponta do cenario principal e dos fluxos alternativos.

Percorre o caminho completo do UC02 ao UC08 pela API, exatamente como a interface
o faz, e verifica os fluxos A1 (sem fundamento) e a trava RN01 na fronteira HTTP.
"""
from __future__ import annotations

import hashlib
import io

from docx import Document

DEMANDA = {
    "unidade_requisitante": "Coordenadoria de Protocolo",
    "titulo": "Aquisição de equipamentos de informática",
    "descricao": (
        "Necessidade de aquisição de 40 notebooks para os laboratórios das escolas, "
        "com prioridade alta, destinados à formalização da demanda e ao planejamento "
        "da contratação no plano de contratações anual."
    ),
}
TODOS_OS_CAMPOS = [
    "justificativa_necessidade",
    "descricao_objeto",
    "quantidade",
    "estimativa_valor",
    "data_pretendida",
    "grau_prioridade",
    "vinculacao_outro_dfd",
    "area_requisitante",
]


def _novo_dfd_com_minuta(cliente, cabecalho) -> tuple[int, dict]:  # noqa: ANN001
    id_dfd = cliente.post("/demandas", json=DEMANDA, headers=cabecalho).json()["id_dfd"]
    minuta = cliente.post(f"/dfd/{id_dfd}/gerar", json={}, headers=cabecalho).json()
    return id_dfd, minuta


def _aprovar(cliente, cabecalho, id_dfd: int, id_minuta: int):  # noqa: ANN001
    return cliente.post(
        f"/dfd/{id_dfd}/validar",
        json={"id_minuta": id_minuta, "decisao": "aprovado", "campos_confirmados": TODOS_OS_CAMPOS},
        headers=cabecalho,
    )


def test_cenario_principal_ate_a_exportacao(cliente, cabecalho):  # noqa: ANN001
    # ---------------------------------------------------- UC02: registrar
    resposta = cliente.post("/demandas", json=DEMANDA, headers=cabecalho)
    assert resposta.status_code == 201, resposta.text
    id_dfd = resposta.json()["id_dfd"]

    # ---------------------------------------------------- UC03/UC04: gerar
    resposta = cliente.post(f"/dfd/{id_dfd}/gerar", json={}, headers=cabecalho)
    assert resposta.status_code == 200, resposta.text
    minuta = resposta.json()
    assert minuta["versao"] == 1
    assert minuta["fontes"], "a minuta precisa vir com as fontes que a fundamentam"
    assert minuta["conteudo"]["justificativa_necessidade"]["fundamentos"]
    assert minuta["verificacao"]["taxa_ancoragem"] is not None
    assert minuta["analise"]["checklist"], "a analise de conformidade acompanha a minuta"
    assert minuta["situacao"] == "em_elaboracao" and minuta["aprovada"] is False
    # todos os campos do art. 8º existem, mesmo os nao extraidos
    assert set(TODOS_OS_CAMPOS[:-1]) <= set(minuta["conteudo"])

    fontes = {f["marcador"] for f in minuta["fontes"]}
    assert fontes >= {"T1"}

    # ---------------------------------------------------- RN01 antes da validacao
    resposta = cliente.post(f"/dfd/{id_dfd}/exportar", headers=cabecalho)
    assert resposta.status_code == 409
    assert "validação humana" in resposta.json()["detail"]

    # ---------------------------------------------------- UC05: revisar
    conteudo = dict(minuta["conteudo"])
    conteudo["justificativa_necessidade"] = {
        **conteudo["justificativa_necessidade"],
        "texto": conteudo["justificativa_necessidade"]["texto"] + " Texto revisado pelo agente.",
    }
    resposta = cliente.post(
        f"/dfd/{id_dfd}/revisar", json={"conteudo": conteudo}, headers=cabecalho
    )
    assert resposta.status_code == 200, resposta.text
    versao_2 = resposta.json()
    assert versao_2["versao"] == 2
    assert versao_2["origem"] == "edicao"

    # ---------------------------------------------------- UC07: validar
    resposta = _aprovar(cliente, cabecalho, id_dfd, versao_2["id_minuta"])
    assert resposta.status_code == 200, resposta.text

    atual = cliente.get(f"/dfd/{id_dfd}", headers=cabecalho).json()
    assert atual["situacao"] == "validado" and atual["aprovada"] is True

    # ---------------------------------------------------- UC08: exportar
    resposta = cliente.post(f"/dfd/{id_dfd}/exportar", headers=cabecalho)
    assert resposta.status_code == 200, resposta.text
    exportacao = resposta.json()
    assert len(exportacao["hash_arquivo"]) == 64
    assert exportacao["arquivo"].endswith(".docx")

    # ---------------------------------------------------- download do arquivo
    arquivo = cliente.get(exportacao["url_download"], headers=cabecalho)
    assert arquivo.status_code == 200
    assert hashlib.sha256(arquivo.content).hexdigest() == exportacao["hash_arquivo"]
    texto = "\n".join(p.text for p in Document(io.BytesIO(arquivo.content)).paragraphs)
    assert "DOCUMENTO DE FORMALIZAÇÃO DA DEMANDA" in texto
    assert "Fundamentação: " in texto and "Fundamentação: T1" not in texto
    assert "Anexo II" in texto

    # ---------------------------------------------------- auditoria completa
    resposta = cliente.get(f"/auditoria?id_dfd={id_dfd}", headers=cabecalho)
    acoes = {r["acao"] for r in resposta.json()}
    assert {"REGISTRO_DEMANDA", "GERACAO", "EDICAO", "VALIDACAO", "EXPORTACAO"} <= acoes

    # histórico preservado: nada foi sobrescrito
    versoes = cliente.get(f"/dfd/{id_dfd}/versoes", headers=cabecalho).json()
    assert [v["versao"] for v in versoes] == [1, 2]
    assert versoes[1]["decisao"] == "aprovado"

    # exportado: nao admite nova geracao nem edicao
    assert cliente.post(f"/dfd/{id_dfd}/gerar", json={}, headers=cabecalho).status_code == 409
    assert cliente.post(
        f"/dfd/{id_dfd}/revisar", json={"conteudo": conteudo}, headers=cabecalho
    ).status_code == 409


def test_validar_versao_desatualizada_e_recusado(cliente, cabecalho):  # noqa: ANN001
    """Se a minuta mudou depois de exibida, a aprovacao da versao antiga e barrada."""
    id_dfd, v1 = _novo_dfd_com_minuta(cliente, cabecalho)
    editado = dict(v1["conteudo"])
    editado["descricao_objeto"] = {**editado["descricao_objeto"], "texto": "Objeto ajustado."}
    cliente.post(f"/dfd/{id_dfd}/revisar", json={"conteudo": editado}, headers=cabecalho)

    resposta = _aprovar(cliente, cabecalho, id_dfd, v1["id_minuta"])
    assert resposta.status_code == 409
    assert "versão" in resposta.json()["detail"]


def test_revisao_sem_alteracao_nao_cria_versao(cliente, cabecalho):  # noqa: ANN001
    id_dfd, v1 = _novo_dfd_com_minuta(cliente, cabecalho)
    resposta = cliente.post(
        f"/dfd/{id_dfd}/revisar", json={"conteudo": v1["conteudo"]}, headers=cabecalho
    )
    assert resposta.status_code == 200
    assert resposta.json()["versao"] == 1
    assert resposta.json()["versao_criada"] is False


def test_nova_versao_revoga_situacao_validado(cliente, cabecalho):  # noqa: ANN001
    """Aprovar v1 e depois editar: a v2 nao herda a aprovacao."""
    id_dfd, v1 = _novo_dfd_com_minuta(cliente, cabecalho)
    assert _aprovar(cliente, cabecalho, id_dfd, v1["id_minuta"]).status_code == 200

    editado = dict(v1["conteudo"])
    editado["descricao_objeto"] = {**editado["descricao_objeto"], "texto": "Objeto revisto."}
    v2 = cliente.post(
        f"/dfd/{id_dfd}/revisar", json={"conteudo": editado}, headers=cabecalho
    ).json()
    assert v2["situacao"] == "em_elaboracao" and v2["aprovada"] is False
    assert cliente.post(f"/dfd/{id_dfd}/exportar", headers=cabecalho).status_code == 409


def test_aprovacao_exige_todos_os_campos(cliente, cabecalho):  # noqa: ANN001
    """RF07 na API: nao basta a interface desabilitar o botao."""
    id_dfd, v1 = _novo_dfd_com_minuta(cliente, cabecalho)
    resposta = cliente.post(
        f"/dfd/{id_dfd}/validar",
        json={
            "id_minuta": v1["id_minuta"],
            "decisao": "aprovado",
            "campos_confirmados": ["justificativa_necessidade"],
        },
        headers=cabecalho,
    )
    assert resposta.status_code == 422
    assert "Faltam" in resposta.json()["detail"]


def test_devolucao_exige_justificativa_e_revoga_aprovacao(cliente, cabecalho):  # noqa: ANN001
    id_dfd, v1 = _novo_dfd_com_minuta(cliente, cabecalho)
    base = {"id_minuta": v1["id_minuta"], "decisao": "devolvido"}
    assert cliente.post(f"/dfd/{id_dfd}/validar", json=base, headers=cabecalho).status_code == 422

    assert _aprovar(cliente, cabecalho, id_dfd, v1["id_minuta"]).status_code == 200
    resposta = cliente.post(
        f"/dfd/{id_dfd}/validar",
        json={**base, "justificativa": "Detalhar melhor o objeto."},
        headers=cabecalho,
    )
    assert resposta.status_code == 200
    # a devolucao posterior prevalece sobre a aprovacao
    assert cliente.post(f"/dfd/{id_dfd}/exportar", headers=cabecalho).status_code == 409


def test_revisao_minimiza_dados_pessoais(cliente, cabecalho):  # noqa: ANN001
    """RN06 tambem na edicao humana: CPF digitado na revisao nao e gravado."""
    id_dfd, v1 = _novo_dfd_com_minuta(cliente, cabecalho)
    editado = dict(v1["conteudo"])
    editado["descricao_objeto"] = {
        **editado["descricao_objeto"],
        "texto": "Contato do fiscal: CPF 123.456.789-09.",
    }
    editado["estimativa_valor"] = {"valor": "R$ 180.000,00", "evidencia": None}
    v2 = cliente.post(
        f"/dfd/{id_dfd}/revisar", json={"conteudo": editado}, headers=cabecalho
    ).json()
    assert "123.456.789-09" not in v2["conteudo"]["descricao_objeto"]["texto"]
    assert "[CPF]" in v2["conteudo"]["descricao_objeto"]["texto"]
    # valor informado pelo agente: origem registrada e sem alerta de alucinacao
    assert v2["conteudo"]["estimativa_valor"]["origem"] == "agente"
    assert "estimativa_valor" not in v2["verificacao"]["por_campo"]


def test_saida_do_modelo_e_normalizada():
    """Modelo pequeno devolve listas como texto e campos ausentes."""
    from app.core.conteudo import normalizar_conteudo

    conteudo = normalizar_conteudo(
        {
            "justificativa_necessidade": {"texto": "Texto.", "fundamentos": "T1, t2"},
            "descricao_objeto": "Apenas texto",
            "quantidade": "40 unidades",
            "pendencias": ["x"],
        }
    )
    assert conteudo["justificativa_necessidade"]["fundamentos"] == ["T1", "T2"]
    assert conteudo["descricao_objeto"] == {"texto": "Apenas texto", "fundamentos": []}
    assert conteudo["quantidade"]["valor"] == "40 unidades"
    assert conteudo["data_pretendida"] == {"valor": None, "evidencia": None, "origem": None}
    assert "pendencias" not in conteudo


def test_fluxo_a1_demanda_sem_fundamento(cliente, cabecalho):  # noqa: ANN001
    """Sem trecho acima do limiar, o sistema informa em vez de inventar."""
    demanda = {
        "unidade_requisitante": "Recursos Humanos",
        "titulo": "Confraternização de fim de ano",
        "descricao": "Organizar a festa de fim de ano com decoração temática e música.",
    }
    id_dfd = cliente.post("/demandas", json=demanda, headers=cabecalho).json()["id_dfd"]

    resposta = cliente.post(
        f"/dfd/{id_dfd}/gerar", json={"limiar": 0.99}, headers=cabecalho
    )
    assert resposta.status_code == 422
    assert "fundamentos normativos" in resposta.json()["detail"]

    # o bloqueio fica registrado na auditoria (metrica do capitulo)
    eventos = cliente.get(f"/auditoria?id_dfd={id_dfd}", headers=cabecalho).json()
    assert "GERACAO_BARRADA" in {e["acao"] for e in eventos}

    # A1: o agente ajusta a MESMA demanda, sem abrir outro DFD
    ajuste = {**demanda, "descricao": DEMANDA["descricao"]}
    resposta = cliente.put(f"/demandas/{id_dfd}", json=ajuste, headers=cabecalho)
    assert resposta.status_code == 200
    assert resposta.json()["id_dfd"] == id_dfd
    assert cliente.post(f"/dfd/{id_dfd}/gerar", json={}, headers=cabecalho).status_code == 200


def test_dados_pessoais_sao_removidos_antes_da_gravacao(cliente, cabecalho):  # noqa: ANN001
    """RN06: a descricao nunca e persistida com identificador direto."""
    demanda = {
        **DEMANDA,
        "descricao": DEMANDA["descricao"] + " Responsável: CPF 123.456.789-09.",
    }
    resposta = cliente.post("/demandas", json=demanda, headers=cabecalho)
    assert resposta.status_code == 201
    corpo = resposta.json()
    assert "123.456.789-09" not in corpo["descricao"]
    assert "[CPF]" in corpo["descricao"]
    assert corpo["dados_pessoais_removidos"] == {"CPF": 1}


def test_dfd_de_outro_usuario_nao_e_acessivel(
    cliente, cabecalho, cabecalho_outro, cabecalho_gestor  # noqa: ANN001
):
    id_dfd, _ = _novo_dfd_com_minuta(cliente, cabecalho)

    # outro demandante: 404, como se o documento nao existisse
    assert cliente.get(f"/dfd/{id_dfd}", headers=cabecalho_outro).status_code == 404
    assert cliente.get(f"/demandas/{id_dfd}", headers=cabecalho_outro).status_code == 404
    assert cliente.post(f"/dfd/{id_dfd}/gerar", json={}, headers=cabecalho_outro).status_code == 404
    listados = {d["id_dfd"] for d in cliente.get("/demandas", headers=cabecalho_outro).json()}
    assert id_dfd not in listados

    # auditoria alheia tambem nao aparece
    eventos = cliente.get(f"/auditoria?id_dfd={id_dfd}", headers=cabecalho_outro).json()
    assert eventos == []

    # o gestor tem visao ampla
    assert cliente.get(f"/dfd/{id_dfd}", headers=cabecalho_gestor).status_code == 200


def test_parametros_experimentais_restritos_ao_gestor(
    cliente, cabecalho, cabecalho_gestor  # noqa: ANN001
):
    id_dfd = cliente.post("/demandas", json=DEMANDA, headers=cabecalho).json()["id_dfd"]
    assert cliente.post(
        f"/dfd/{id_dfd}/gerar", json={"usar_rag": False}, headers=cabecalho
    ).status_code == 403
    assert cliente.post(
        f"/dfd/{id_dfd}/gerar", json={"provedor": "desconhecido"}, headers=cabecalho_gestor
    ).status_code == 422
    assert cliente.post(
        f"/dfd/{id_dfd}/gerar", json={"usar_rag": False}, headers=cabecalho_gestor
    ).status_code == 200


def test_indicadores_do_gestor(cliente, cabecalho, cabecalho_gestor):  # noqa: ANN001
    _novo_dfd_com_minuta(cliente, cabecalho)
    assert cliente.get("/indicadores", headers=cabecalho).status_code == 403
    dados = cliente.get("/indicadores", headers=cabecalho_gestor).json()
    assert dados["geracao"]["concluidas"] >= 1
    assert dados["geracao"]["ancoragem_media"] is not None
    assert dados["dfds"]["total"] >= 1


def test_login_bloqueia_apos_tentativas(cliente):  # noqa: ANN001
    corpo = {"email": "ninguem@orgao.gov.br", "senha": "errada"}
    codigos = [cliente.post("/auth/login", json=corpo).status_code for _ in range(6)]
    assert codigos[:5] == [401] * 5
    assert codigos[5] == 429


def test_rotas_exigem_autenticacao(cliente):  # noqa: ANN001
    assert cliente.get("/demandas").status_code == 401
    assert cliente.post("/dfd/1/gerar", json={}).status_code == 401
    assert cliente.get("/auditoria").status_code == 401
    assert cliente.get("/auditoria", headers={"Authorization": "Bearer lixo"}).status_code == 401
