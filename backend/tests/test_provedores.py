"""Provedores Gemini e Groq com a API simulada: nenhum teste usa rede nem chave real."""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import config
from app.llm import provedores
from app.llm.base import ErroProvedorLLM
from app.llm.factory import obter_provedor
from app.llm.provedores import ProvedorGemini, ProvedorGroq

MINUTA = {"justificativa_necessidade": {"texto": "x", "fundamentos": ["T1"]}}


def _resposta(status: int, corpo: dict) -> httpx.Response:
    return httpx.Response(status, json=corpo, request=httpx.Request("POST", "https://teste"))


def _sucesso(**candidato) -> dict:
    return {
        "candidates": [{
            "content": {"parts": [
                {"text": "rascunho interno", "thought": True},
                {"text": json.dumps(MINUTA)},
            ]},
            "finishReason": "STOP",
            **candidato,
        }],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 40, "thoughtsTokenCount": 15},
        "modelVersion": "gemini-3.8-flash",
        "responseId": "r1",
    }


@pytest.fixture
def chamadas(monkeypatch):
    """Substitui httpx.post e devolve as respostas da fila, registrando cada envio."""
    registro: dict = {"fila": [], "enviados": []}

    def falso_post(url, json=None, headers=None, timeout=None):
        registro["enviados"].append({"url": url, "json": json, "headers": headers})
        return registro["fila"].pop(0)

    monkeypatch.setattr(provedores.httpx, "post", falso_post)
    monkeypatch.setattr(provedores.time, "sleep", lambda _s: None)
    return registro


def test_gemini_exige_chave(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(ErroProvedorLLM, match="GEMINI_API_KEY"):
        obter_provedor("gemini")


def test_gemini_monta_requisicao_e_ignora_raciocinio(chamadas):
    chamadas["fila"].append(_resposta(200, _sucesso()))
    resposta = ProvedorGemini(modelo="gemini-3.8-flash", api_key="chave-falsa").completar("sis", "usr")

    enviado = chamadas["enviados"][0]
    assert enviado["url"].endswith("/models/gemini-3.8-flash:generateContent")
    assert enviado["headers"] == {"x-goog-api-key": "chave-falsa"}
    geracao = enviado["json"]["generationConfig"]
    assert geracao["responseMimeType"] == "application/json"
    assert "temperature" not in geracao            # Gemini 3: temperatura padrao
    assert resposta.como_json() == MINUTA           # a parte "thought" fica de fora
    assert resposta.parametros["tokens_raciocinio"] == 15
    assert resposta.modelo == "gemini-3.8-flash"


def test_gemini_antigo_recebe_temperatura(chamadas):
    chamadas["fila"].append(_resposta(200, _sucesso()))
    ProvedorGemini(modelo="gemini-2.5-flash", api_key="k").completar("sis", "usr")
    geracao = chamadas["enviados"][0]["json"]["generationConfig"]
    assert geracao["temperature"] == config.LLM_TEMPERATURA
    assert "thinkingConfig" not in geracao


def test_gemini_repete_apos_429(chamadas):
    limite = {"error": {"message": "quota", "details": [{"retryDelay": "3s"}]}}
    chamadas["fila"] += [_resposta(429, limite), _resposta(200, _sucesso())]
    resposta = ProvedorGemini(api_key="k").completar("sis", "usr")
    assert len(chamadas["enviados"]) == 2
    assert resposta.como_json() == MINUTA


def test_gemini_cota_diaria_falha_sem_esperar(chamadas):
    cota = {"error": {"message": "quota", "details": [
        {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                         "quotaValue": "20"}]},
        {"retryDelay": "54319s"},
    ]}}
    chamadas["fila"].append(_resposta(429, cota))
    with pytest.raises(ErroProvedorLLM, match="cota diaria"):
        ProvedorGemini(api_key="k").completar("sis", "usr")
    assert len(chamadas["enviados"]) == 1


@pytest.mark.parametrize(
    ("status", "corpo", "mensagem"),
    [
        (400, {"error": {"message": "API key not valid"}}, "chave do Gemini"),
        (404, {"error": {"message": "not found"}}, "modelo inexistente"),
        (200, _sucesso(finishReason="MAX_TOKENS"), "truncada"),
        (200, _sucesso(finishReason="SAFETY"), "recusou"),
        (200, {"promptFeedback": {"blockReason": "SAFETY"}}, "recusou"),
    ],
)
def test_gemini_falhas_viram_erro_do_provedor(chamadas, status, corpo, mensagem):
    chamadas["fila"].append(_resposta(status, corpo))
    with pytest.raises(ErroProvedorLLM, match=mensagem):
        ProvedorGemini(api_key="k").completar("sis", "usr")


# ------------------------------------------------------------------ Groq
def _groq(status: int, corpo: dict, **cabecalhos: str) -> httpx.Response:
    return httpx.Response(
        status, json=corpo, headers=cabecalhos, request=httpx.Request("POST", "https://teste")
    )


def _groq_sucesso(finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl-1",
        "model": "openai/gpt-oss-120b",
        "choices": [{
            "message": {"role": "assistant", "content": json.dumps(MINUTA),
                        "reasoning": "rascunho interno"},
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": 300, "completion_tokens": 80},
    }


def test_groq_exige_chave(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    with pytest.raises(ErroProvedorLLM, match="GROQ_API_KEY"):
        obter_provedor("groq")


def test_groq_monta_requisicao_no_formato_openai(chamadas):
    chamadas["fila"].append(_groq(200, _groq_sucesso()))
    resposta = ProvedorGroq(modelo="openai/gpt-oss-120b", api_key="chave-falsa").completar(
        "sis", "usr"
    )

    enviado = chamadas["enviados"][0]
    assert enviado["url"].endswith("/openai/v1/chat/completions")
    assert enviado["headers"] == {"Authorization": "Bearer chave-falsa"}
    corpo = enviado["json"]
    assert corpo["model"] == "openai/gpt-oss-120b"
    assert corpo["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in corpo["messages"]] == ["system", "user"]
    assert resposta.como_json() == MINUTA            # o raciocinio fica de fora
    assert resposta.parametros["provedor"] == "groq"
    assert resposta.parametros["tokens_entrada"] == 300


def test_groq_texto_livre_sem_formato_json(chamadas):
    chamadas["fila"].append(_groq(200, _groq_sucesso()))
    ProvedorGroq(api_key="k").completar("sis", "usr", formato_json=False)
    assert "response_format" not in chamadas["enviados"][0]["json"]


def test_groq_repete_apos_429_com_retry_after(chamadas):
    chamadas["fila"] += [
        _groq(429, {"error": {"message": "rate limit"}}, **{"retry-after": "2"}),
        _groq(200, _groq_sucesso()),
    ]
    resposta = ProvedorGroq(api_key="k").completar("sis", "usr")
    assert len(chamadas["enviados"]) == 2
    assert resposta.como_json() == MINUTA


def test_groq_espera_longa_falha_sem_esperar(chamadas):
    chamadas["fila"].append(
        _groq(429, {"error": {"message": "tokens per day"}}, **{"retry-after": "3600"})
    )
    with pytest.raises(ErroProvedorLLM, match="cota diaria"):
        ProvedorGroq(api_key="k").completar("sis", "usr")
    assert len(chamadas["enviados"]) == 1


def test_groq_repete_json_malformado_pelo_modelo(chamadas):
    chamadas["fila"] += [
        _groq(400, {"error": {"message": "Failed to generate JSON", "code": "json_validate_failed"}}),
        _groq(200, _groq_sucesso()),
    ]
    assert ProvedorGroq(api_key="k").completar("sis", "usr").como_json() == MINUTA


@pytest.mark.parametrize(
    ("status", "corpo", "mensagem"),
    [
        (401, {"error": {"message": "Invalid API Key"}}, "chave da Groq"),
        (404, {"error": {"message": "model not found"}}, "modelo inexistente"),
        (413, {"error": {"message": "Request too large"}}, "tokens por minuto"),
        (400, {"error": {"message": "bad"}}, "recusada"),
        (200, _groq_sucesso(finish_reason="length"), "truncada"),
        (200, {"choices": []}, "vazia"),
    ],
)
def test_groq_falhas_viram_erro_do_provedor(chamadas, status, corpo, mensagem):
    chamadas["fila"].append(_groq(status, corpo))
    with pytest.raises(ErroProvedorLLM, match=mensagem):
        ProvedorGroq(api_key="k").completar("sis", "usr")
