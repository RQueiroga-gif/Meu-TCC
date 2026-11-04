"""Provedores concretos de modelo de linguagem.

Cinco implementacoes da interface ProvedorLLM:

  * ProvedorAnthropic — modelo comercial pelo SDK oficial (gerador principal);
  * ProvedorGemini — modelo comercial do Google pela API REST do Gemini;
  * ProvedorGroq — modelos abertos hospedados pela Groq (API no formato OpenAI);
  * ProvedorOllama — modelo aberto executado localmente, modo em que nenhum dado
    deixa a maquina; usado no Experimento 2 (nuvem x local) e na depuracao;
  * ProvedorEco — implementacao deterministica, sem rede, que monta a minuta a
    partir dos trechos recuperados. Serve para exercitar o fluxo completo e os
    testes automatizados sem chave de API e sem custo. Nao produz texto de
    qualidade e nunca deve alimentar as metricas do capitulo de resultados.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from ..config import config
from .base import ErroProvedorLLM, ProvedorLLM, Resposta

log = logging.getLogger(__name__)

# Modelos que ainda aceitam parametros de amostragem (temperature). Nos modelos
# mais novos (Opus 4.7+, Sonnet 5+, Fable) o parametro e rejeitado com 400.
RE_ACEITA_TEMPERATURA = re.compile(r"^claude-(haiku-4|sonnet-4|opus-4-[0-6]|3)")


class ProvedorAnthropic(ProvedorLLM):
    """Modelo comercial acessado pelo SDK oficial da Anthropic.

    O SDK repete automaticamente as falhas transitorias (conexao, 408, 409, 429,
    5xx e 529 "overloaded") com espera exponencial; LLM_TENTATIVAS controla
    quantas vezes.
    """

    nome = "anthropic"

    def __init__(self, modelo: str | None = None, api_key: str | None = None) -> None:
        import anthropic   # import tardio: dependencia so necessaria para este provedor

        self._anthropic = anthropic
        self._modelo = modelo or config.LLM_MODELO
        try:
            # Sem chave explicita, o SDK tenta as demais credenciais do ambiente
            # (ANTHROPIC_AUTH_TOKEN, perfil do `ant auth login`).
            self._cliente = anthropic.Anthropic(
                api_key=api_key or config.ANTHROPIC_API_KEY or None,
                timeout=float(config.LLM_TIMEOUT_S),
                max_retries=config.LLM_TENTATIVAS,
            )
        except anthropic.AnthropicError as erro:
            raise ErroProvedorLLM(
                "Credencial da Anthropic nao encontrada. Defina ANTHROPIC_API_KEY no .env "
                "ou use LLM_PROVEDOR=ollama (local) ou LLM_PROVEDOR=echo (offline)."
            ) from erro

    def _parametros(self) -> dict[str, Any]:
        if RE_ACEITA_TEMPERATURA.match(self._modelo):
            return {"temperature": config.LLM_TEMPERATURA}
        return {}

    def completar(
        self, prompt_sistema: str, prompt_usuario: str, *, formato_json: bool = True
    ) -> Resposta:
        anthropic = self._anthropic
        try:
            mensagem = self._cliente.messages.create(
                model=self._modelo,
                max_tokens=config.LLM_MAX_TOKENS,
                system=prompt_sistema,
                messages=[{"role": "user", "content": prompt_usuario}],
                **self._parametros(),
            )
        except anthropic.AuthenticationError as erro:
            raise ErroProvedorLLM("chave da Anthropic invalida ou revogada") from erro
        except anthropic.NotFoundError as erro:
            raise ErroProvedorLLM(f"modelo inexistente: {self._modelo}") from erro
        except anthropic.BadRequestError as erro:
            raise ErroProvedorLLM(f"requisicao recusada pelo provedor: {erro.message}") from erro
        except anthropic.RateLimitError as erro:
            raise ErroProvedorLLM("limite de uso do provedor atingido; tente mais tarde") from erro
        except anthropic.APIStatusError as erro:
            raise ErroProvedorLLM(f"provedor respondeu {erro.status_code}") from erro
        except anthropic.APIConnectionError as erro:
            raise ErroProvedorLLM(f"falha de comunicacao com o provedor: {erro}") from erro

        if mensagem.stop_reason == "refusal":
            raise ErroProvedorLLM("o modelo recusou a solicitacao")
        if mensagem.stop_reason == "max_tokens":
            raise ErroProvedorLLM(
                "resposta do modelo truncada (LLM_MAX_TOKENS insuficiente)"
            )

        texto = "".join(b.text for b in mensagem.content if b.type == "text")
        if not texto.strip():
            raise ErroProvedorLLM("provedor devolveu resposta vazia")

        return Resposta(
            texto=texto,
            modelo=mensagem.model,
            parametros={
                **{("temperatura" if k == "temperature" else k): v for k, v in self._parametros().items()},
                "provedor": self.nome,
                "tokens_entrada": mensagem.usage.input_tokens,
                "tokens_saida": mensagem.usage.output_tokens,
                "id_requisicao": getattr(mensagem, "_request_id", None),
            },
        )


class ProvedorGemini(ProvedorLLM):
    """Modelo comercial do Google acessado pela API REST do Gemini (generateContent).

    Nos modelos Gemini 3 o fabricante recomenda manter a temperatura padrao (1.0):
    valores menores causam repeticao em tarefas de raciocinio. Por isso a
    temperatura so e enviada aos modelos anteriores (1.x e 2.x). O raciocinio
    interno ("thinking") consome parte de LLM_MAX_TOKENS e e limitado por
    GEMINI_NIVEL_RACIOCINIO.

    Atencao (LGPD): no nivel gratuito da API, o Google pode usar o conteudo
    enviado para melhorar seus produtos. Use-o apenas com demandas ficticias,
    como as do gabarito da avaliacao.
    """

    nome = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
    RE_ACEITA_TEMPERATURA = re.compile(r"^gemini-[12]\.")
    # Motivos de parada que significam recusa ou bloqueio do conteudo.
    MOTIVOS_RECUSA = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}

    def __init__(self, modelo: str | None = None, api_key: str | None = None) -> None:
        self._modelo = modelo or config.GEMINI_MODELO
        self._chave = api_key or config.GEMINI_API_KEY
        if not self._chave:
            raise ErroProvedorLLM(
                "Credencial do Gemini nao encontrada. Defina GEMINI_API_KEY no .env "
                "ou use LLM_PROVEDOR=ollama (local) ou LLM_PROVEDOR=echo (offline)."
            )

    def _configuracao(self, formato_json: bool = True) -> dict[str, Any]:
        geracao: dict[str, Any] = {"maxOutputTokens": config.LLM_MAX_TOKENS}
        if formato_json:
            geracao["responseMimeType"] = "application/json"
        if self.RE_ACEITA_TEMPERATURA.match(self._modelo):
            geracao["temperature"] = config.LLM_TEMPERATURA
        elif config.GEMINI_NIVEL_RACIOCINIO:
            geracao["thinkingConfig"] = {"thinkingLevel": config.GEMINI_NIVEL_RACIOCINIO}
        return geracao

    ESPERA_MAXIMA_S = 90.0

    @classmethod
    def _espera_sugerida(cls, resposta: httpx.Response, tentativa: int) -> float:
        """Tempo de espera apos 429/503: usa o retryDelay informado pela API, se houver.

        Raises:
            ErroProvedorLLM: cota diaria esgotada (esperar nao adianta).
        """
        atraso: float | None = None
        try:
            for detalhe in resposta.json().get("error", {}).get("details", []):
                for violacao in detalhe.get("violations", []):
                    if "PerDay" in str(violacao.get("quotaId", "")):
                        raise ErroProvedorLLM(
                            "cota diaria do Gemini esgotada para o modelo "
                            f"(limite {violacao.get('quotaValue', '?')} chamadas/dia); "
                            "tente amanha, use outro GEMINI_MODELO ou ative o faturamento"
                        )
                if valor := detalhe.get("retryDelay"):
                    atraso = float(str(valor).rstrip("s"))
        except (ValueError, AttributeError):
            pass
        if atraso is not None and atraso > cls.ESPERA_MAXIMA_S:
            raise ErroProvedorLLM(
                f"limite de uso do provedor atingido; nova tentativa so em {atraso:.0f} s"
            )
        return atraso if atraso is not None else min(10.0 * 2 ** tentativa, cls.ESPERA_MAXIMA_S)

    def completar(
        self, prompt_sistema: str, prompt_usuario: str, *, formato_json: bool = True
    ) -> Resposta:
        geracao = self._configuracao(formato_json)
        corpo = {
            "systemInstruction": {"parts": [{"text": prompt_sistema}]},
            "contents": [{"role": "user", "parts": [{"text": prompt_usuario}]}],
            "generationConfig": geracao,
        }
        cabecalhos = {"x-goog-api-key": self._chave}
        url = self.URL.format(modelo=self._modelo)

        dados: dict[str, Any] = {}
        ultimo_erro: str = ""
        for tentativa in range(max(config.LLM_TENTATIVAS, 0) + 1):
            try:
                resposta = httpx.post(
                    url, json=corpo, headers=cabecalhos, timeout=config.LLM_TIMEOUT_S
                )
            except httpx.HTTPError as erro:
                ultimo_erro = f"falha de comunicacao com o provedor: {erro}"
                if tentativa < config.LLM_TENTATIVAS:
                    time.sleep(2 ** tentativa)
                continue

            if resposta.status_code == 200:
                dados = resposta.json()
                break
            try:
                mensagem = str(resposta.json().get("error", {}).get("message", ""))
            except (ValueError, AttributeError):
                mensagem = ""
            if resposta.status_code in (401, 403) or "API key" in mensagem:
                raise ErroProvedorLLM("chave do Gemini invalida, revogada ou sem permissao")
            if resposta.status_code == 404:
                raise ErroProvedorLLM(f"modelo inexistente: {self._modelo}")
            if resposta.status_code == 400:
                raise ErroProvedorLLM(f"requisicao recusada pelo provedor: {mensagem}")
            if resposta.status_code in (429, 503):
                # 429: cota do plano; 503: modelo sobrecarregado (picos passageiros)
                ultimo_erro = (
                    "limite de uso do provedor atingido; tente mais tarde"
                    if resposta.status_code == 429
                    else "modelo sobrecarregado no provedor (503); tente mais tarde"
                )
                if tentativa < config.LLM_TENTATIVAS:
                    time.sleep(self._espera_sugerida(resposta, tentativa))
                continue
            ultimo_erro = f"provedor respondeu {resposta.status_code}"
            if tentativa < config.LLM_TENTATIVAS:
                time.sleep(2 ** tentativa)
        else:
            raise ErroProvedorLLM(ultimo_erro)

        if bloqueio := dados.get("promptFeedback", {}).get("blockReason"):
            raise ErroProvedorLLM(f"o modelo recusou a solicitacao ({bloqueio})")
        candidatos = dados.get("candidates") or []
        if not candidatos:
            raise ErroProvedorLLM("provedor devolveu resposta vazia")
        motivo = candidatos[0].get("finishReason", "")
        if motivo in self.MOTIVOS_RECUSA:
            raise ErroProvedorLLM(f"o modelo recusou a solicitacao ({motivo})")
        if motivo == "MAX_TOKENS":
            raise ErroProvedorLLM(
                "resposta do modelo truncada (LLM_MAX_TOKENS insuficiente)"
            )

        partes = candidatos[0].get("content", {}).get("parts", [])
        texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
        if not texto.strip():
            raise ErroProvedorLLM("provedor devolveu resposta vazia")

        uso = dados.get("usageMetadata", {})
        parametros: dict[str, Any] = {"provedor": self.nome}
        if "temperature" in geracao:
            parametros["temperatura"] = geracao["temperature"]
        if "thinkingConfig" in geracao:
            parametros["nivel_raciocinio"] = geracao["thinkingConfig"]["thinkingLevel"]
        parametros.update(
            tokens_entrada=uso.get("promptTokenCount"),
            tokens_saida=uso.get("candidatesTokenCount"),
            tokens_raciocinio=uso.get("thoughtsTokenCount"),
            id_requisicao=dados.get("responseId"),
        )
        return Resposta(
            texto=texto,
            modelo=dados.get("modelVersion") or self._modelo,
            parametros=parametros,
        )


class ProvedorGroq(ProvedorLLM):
    """Modelos abertos hospedados pela Groq, pela API compativel com a da OpenAI.

    Executa modelos de pesos abertos (gpt-oss, Qwen, Llama) com baixa latencia.
    O nivel gratuito limita requisicoes e tokens por minuto e por dia; um 429
    informa no cabecalho retry-after quanto esperar.

    Atencao (LGPD): o conteudo sai do ambiente. Leia a politica de dados da Groq
    e use apenas demandas ficticias enquanto ela nao estiver documentada no TCC.
    """

    nome = "groq"
    ESPERA_MAXIMA_S = 90.0

    def __init__(self, modelo: str | None = None, api_key: str | None = None) -> None:
        self._modelo = modelo or config.GROQ_MODELO
        self._chave = api_key or config.GROQ_API_KEY
        self._url = config.GROQ_URL.rstrip("/") + "/chat/completions"
        if not self._chave:
            raise ErroProvedorLLM(
                "Credencial da Groq nao encontrada. Defina GROQ_API_KEY no .env "
                "ou use LLM_PROVEDOR=ollama (local) ou LLM_PROVEDOR=echo (offline)."
            )

    @classmethod
    def _espera_sugerida(cls, resposta: httpx.Response, tentativa: int) -> float:
        """Tempo de espera apos 429/503, pelo cabecalho retry-after quando houver.

        Raises:
            ErroProvedorLLM: espera longa demais (cota diaria do nivel gratuito).
        """
        try:
            atraso = float(resposta.headers.get("retry-after", ""))
        except ValueError:
            return min(5.0 * 2 ** tentativa, cls.ESPERA_MAXIMA_S)
        if atraso > cls.ESPERA_MAXIMA_S:
            raise ErroProvedorLLM(
                f"limite de uso da Groq atingido; nova tentativa so em {atraso:.0f} s "
                "(cota diaria do nivel gratuito?)"
            )
        return atraso

    def completar(
        self, prompt_sistema: str, prompt_usuario: str, *, formato_json: bool = True
    ) -> Resposta:
        corpo: dict[str, Any] = {
            "model": self._modelo,
            "temperature": config.LLM_TEMPERATURA,
            "max_completion_tokens": config.LLM_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": prompt_usuario},
            ],
        }
        if formato_json:
            corpo["response_format"] = {"type": "json_object"}
        cabecalhos = {"Authorization": f"Bearer {self._chave}"}

        dados: dict[str, Any] = {}
        ultimo_erro = ""
        for tentativa in range(max(config.LLM_TENTATIVAS, 0) + 1):
            try:
                resposta = httpx.post(
                    self._url, json=corpo, headers=cabecalhos, timeout=config.LLM_TIMEOUT_S
                )
            except httpx.HTTPError as erro:
                ultimo_erro = f"falha de comunicacao com o provedor: {erro}"
                if tentativa < config.LLM_TENTATIVAS:
                    time.sleep(2 ** tentativa)
                continue

            if resposta.status_code == 200:
                dados = resposta.json()
                break
            try:
                erro_api = resposta.json().get("error", {}) or {}
            except (ValueError, AttributeError):
                erro_api = {}
            mensagem = str(erro_api.get("message", ""))
            if resposta.status_code in (401, 403):
                raise ErroProvedorLLM("chave da Groq invalida, revogada ou sem permissao")
            if resposta.status_code == 404:
                raise ErroProvedorLLM(f"modelo inexistente na Groq: {self._modelo}")
            if resposta.status_code == 413:
                raise ErroProvedorLLM(
                    "requisicao maior que o limite de tokens por minuto do plano da Groq; "
                    "reduza RECUPERACAO_TOP_K ou LLM_MAX_TOKENS"
                )
            if resposta.status_code == 400 and erro_api.get("code") != "json_validate_failed":
                raise ErroProvedorLLM(f"requisicao recusada pelo provedor: {mensagem}")
            if resposta.status_code in (429, 503):
                ultimo_erro = (
                    "limite de uso do provedor atingido; tente mais tarde"
                    if resposta.status_code == 429
                    else "modelo sobrecarregado no provedor (503); tente mais tarde"
                )
                if tentativa < config.LLM_TENTATIVAS:
                    time.sleep(self._espera_sugerida(resposta, tentativa))
                continue
            # 5xx ou JSON malformado pelo modelo (json_validate_failed): repetir
            ultimo_erro = f"provedor respondeu {resposta.status_code}: {mensagem[:200]}"
            if tentativa < config.LLM_TENTATIVAS:
                time.sleep(2 ** tentativa)
        else:
            raise ErroProvedorLLM(ultimo_erro)

        escolhas = dados.get("choices") or []
        if not escolhas:
            raise ErroProvedorLLM("provedor devolveu resposta vazia")
        motivo = escolhas[0].get("finish_reason")
        if motivo == "length":
            raise ErroProvedorLLM("resposta do modelo truncada (LLM_MAX_TOKENS insuficiente)")
        if motivo == "content_filter":
            raise ErroProvedorLLM("o modelo recusou a solicitacao (content_filter)")
        texto = (escolhas[0].get("message") or {}).get("content") or ""
        if not texto.strip():
            raise ErroProvedorLLM("provedor devolveu resposta vazia")

        uso = dados.get("usage") or {}
        return Resposta(
            texto=texto,
            modelo=dados.get("model") or self._modelo,
            parametros={
                "temperatura": config.LLM_TEMPERATURA,
                "provedor": self.nome,
                "tokens_entrada": uso.get("prompt_tokens"),
                "tokens_saida": uso.get("completion_tokens"),
                "id_requisicao": dados.get("id"),
            },
        )


class ProvedorOllama(ProvedorLLM):
    """Modelo aberto executado localmente: nenhum dado deixa o ambiente."""

    nome = "ollama"

    def __init__(self, modelo: str | None = None, url: str | None = None) -> None:
        self._modelo = modelo or config.OLLAMA_MODELO
        self._url = (url or config.OLLAMA_URL).rstrip("/")

    def completar(
        self, prompt_sistema: str, prompt_usuario: str, *, formato_json: bool = True
    ) -> Resposta:
        corpo: dict[str, Any] = {
            "model": self._modelo,
            "stream": False,
            "options": {"temperature": config.LLM_TEMPERATURA},
            "messages": [
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": prompt_usuario},
            ],
        }
        if formato_json:
            corpo["format"] = "json"
        ultimo_erro: Exception | None = None
        for tentativa in range(max(config.LLM_TENTATIVAS, 0) + 1):
            try:
                resposta = httpx.post(
                    f"{self._url}/api/chat", json=corpo, timeout=config.LLM_TIMEOUT_S
                )
                if resposta.status_code == 404:
                    raise ErroProvedorLLM(
                        f"modelo '{self._modelo}' nao encontrado no Ollama "
                        f"(execute: ollama pull {self._modelo})"
                    )
                resposta.raise_for_status()
                texto = resposta.json().get("message", {}).get("content", "")
                break
            except httpx.HTTPError as erro:
                ultimo_erro = erro
                if tentativa < config.LLM_TENTATIVAS:
                    time.sleep(2 ** tentativa)
        else:
            raise ErroProvedorLLM(
                f"Ollama indisponivel em {self._url}: {ultimo_erro}. "
                "Verifique se o servico esta em execucao (ollama serve)."
            ) from ultimo_erro

        if not texto.strip():
            raise ErroProvedorLLM("Ollama devolveu resposta vazia")

        return Resposta(
            texto=texto,
            modelo=self._modelo,
            parametros={"temperatura": config.LLM_TEMPERATURA, "provedor": self.nome},
        )


class ProvedorEco(ProvedorLLM):
    """Gerador deterministico, sem rede, para desenvolvimento e testes.

    Monta a minuta a partir do proprio contexto recebido: cita os dois primeiros
    marcadores como fundamentos e extrai da demanda, por expressao regular,
    quantidade, prioridade, valor e data, sempre copiando o trecho literal como
    evidencia. Com isso, a saida passa pela mesma verificacao (RN02) que a de um
    modelo real.
    """

    nome = "echo"

    RE_QUANTIDADE = re.compile(r"\b(\d{1,6})\s+([a-zà-ú][\wà-ú-]{2,})", re.I)
    RE_PRIORIDADE = re.compile(r"prioridade\s+(alta|m[ée]dia|baixa)", re.I)
    RE_VALOR = re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?", re.I)
    RE_DATA = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b|\b\d{1,2}/\d{4}\b")
    PRIORIDADE = {"alta": "alto", "media": "médio", "média": "médio", "baixa": "baixo"}

    def completar(
        self, prompt_sistema: str, prompt_usuario: str, *, formato_json: bool = True
    ) -> Resposta:
        demanda = self._extrair(prompt_usuario, "DEMANDA", "CONTEXTO")
        contexto = self._extrair(prompt_usuario, "CONTEXTO", "FORMATO DA RESPOSTA")
        marcadores = re.findall(r"\[(T\d+)\]", contexto)
        referencias = re.findall(r"\[T\d+\]\s*([^—\n]+)—", contexto)
        citacao = referencias[0].strip() if referencias else "o marco normativo aplicável"
        corpo = demanda.split("\n", 1)[1].strip() if "\n" in demanda else demanda

        minuta: dict[str, Any] = {
            "justificativa_necessidade": {
                "texto": (
                    "A presente contratação justifica-se pela necessidade administrativa "
                    "descrita pela unidade requisitante, em observância ao planejamento "
                    f"exigido por {citacao}. {corpo[:400]}"
                ).strip(),
                "fundamentos": marcadores[:2],
            },
            "descricao_objeto": {
                "texto": (
                    "O objeto corresponde ao atendimento da necessidade registrada, "
                    "descrito de forma sucinta e suficiente para a fase preparatória."
                ),
                "fundamentos": marcadores[:1],
            },
        }

        for campo in (
            "quantidade",
            "estimativa_valor",
            "data_pretendida",
            "grau_prioridade",
            "vinculacao_outro_dfd",
        ):
            minuta[campo] = {"valor": None, "evidencia": None}

        if achado := self.RE_QUANTIDADE.search(demanda):
            minuta["quantidade"] = {
                "valor": f"{achado.group(1)} {achado.group(2)}",
                "evidencia": achado.group(0),
            }
        if achado := self.RE_PRIORIDADE.search(demanda):
            minuta["grau_prioridade"] = {
                "valor": self.PRIORIDADE.get(achado.group(1).lower(), achado.group(1).lower()),
                "evidencia": achado.group(0),
            }
        if achado := self.RE_VALOR.search(demanda):
            minuta["estimativa_valor"] = {"valor": achado.group(0), "evidencia": achado.group(0)}
        if achado := self.RE_DATA.search(demanda):
            minuta["data_pretendida"] = {"valor": achado.group(0), "evidencia": achado.group(0)}

        minuta["pendencias"] = [
            campo
            for campo in (
                "quantidade",
                "estimativa_valor",
                "data_pretendida",
                "grau_prioridade",
                "vinculacao_outro_dfd",
            )
            if minuta[campo]["valor"] is None
        ]

        return Resposta(
            texto=json.dumps(minuta, ensure_ascii=False),
            modelo="echo-deterministico",
            parametros={"provedor": self.nome, "temperatura": 0.0},
        )

    @staticmethod
    def _extrair(texto: str, inicio: str, fim: str) -> str:
        i = texto.find(inicio)
        f = texto.find(fim)
        if i == -1:
            return ""
        return texto[i : f if f > i else len(texto)]


__all__ = ["ProvedorAnthropic", "ProvedorGemini", "ProvedorGroq", "ProvedorOllama", "ProvedorEco"]
