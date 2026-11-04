"""Revisao de redacao da minuta por um segundo modelo de linguagem.

O revisor trabalha apenas nos campos REDIGIDOS (I e II do art. 8º): corrige
ortografia, gramatica, pontuacao e clareza. Os campos extraidos (III a VII) nao
passam por ele, porque sao fatos da demanda sustentados por evidencia literal.

A correcao e uma SUGESTAO: nada e gravado aqui. O agente publico compara o texto
original com o sugerido e, se aceitar, a sugestao vira versao pelo fluxo de
revisao (UC05), com a mesma verificacao de fundamentos de qualquer edicao.

Antes disso, tres salvaguardas descartam a sugestao de um campo:
  * mencoes a artigos ou normas diferentes das do original — o revisor corrige a
    forma, nao o fundamento juridico (RN02);
  * numeros diferentes dos do original (quantidade, valor, data, prazo) — fatos
    da demanda nao sao materia de revisao de redacao;
  * texto muito maior que o original — sinal de conteudo acrescentado.

O provedor e escolhido por LLM_PROVEDOR_CORRECAO, de modo que o texto pode ser
revisado por um fornecedor diferente do que o redigiu.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import config
from ..core.anonimizador import minimizar
from ..core.verificacao import CAMPOS_REDIGIDOS, mencoes, normas_mencionadas
from ..llm.base import ErroProvedorLLM, ProvedorLLM, RespostaInvalida
from ..llm.factory import obter_provedor

log = logging.getLogger(__name__)

VERSAO = "c-1.0"

PROMPT_SISTEMA = """\
Você revisa a redação de minutas de Documentos de Formalização da Demanda (DFD) \
da Administração Pública brasileira.

Para cada campo recebido:
1. Corrija ortografia, acentuação, gramática, concordância e pontuação.
2. Melhore a clareza e a coesão, em linguagem formal, impessoal e objetiva.
3. Preserve o sentido. Não acrescente fatos, quantidades, valores, datas, \
justificativas ou argumentos que não estejam no texto.
4. Não inclua, remova nem altere menções a artigos, leis, decretos ou outras \
normas: mantenha exatamente as que já estão no texto.
5. Se o texto já estiver correto, devolva-o sem alteração.

Responda exclusivamente com um objeto JSON no formato especificado, sem \
comentários e sem texto fora do JSON."""

MODELO_SAIDA: dict[str, Any] = {
    "campos": {campo: "texto revisado" for campo in CAMPOS_REDIGIDOS},
    "observacoes": ["descrição breve de cada alteração relevante"],
}

# Numeros como aparecem no texto: "180.000,00", "30/06/2027", "12", "8º".
RE_NUMERO = re.compile(r"\d+(?:[.,/]\d+)*")

# O texto revisado pode crescer um pouco (pontuacao, conectivos), nao dobrar.
CRESCIMENTO_MAXIMO = 1.5
FOLGA_CARACTERES = 200


@dataclass(slots=True)
class CampoCorrigido:
    campo: str
    original: str
    sugerido: str
    motivo_descarte: str | None = None

    @property
    def alterado(self) -> bool:
        return self.sugerido != self.original


@dataclass(slots=True)
class SugestaoCorrecao:
    campos: list[CampoCorrigido]
    observacoes: list[str]
    provedor: str
    modelo: str
    parametros: dict[str, Any] = field(default_factory=dict)

    def aplicar(self, conteudo: dict[str, Any]) -> dict[str, Any]:
        """Conteudo da minuta com os textos sugeridos (fundamentos preservados)."""
        saida = {k: dict(v) if isinstance(v, dict) else v for k, v in conteudo.items()}
        for c in self.campos:
            campo = saida.get(c.campo) if isinstance(saida.get(c.campo), dict) else {}
            saida[c.campo] = {**campo, "texto": c.sugerido}
        return saida


def _texto_do_campo(conteudo: dict[str, Any], nome: str) -> str:
    campo = conteudo.get(nome)
    texto = campo.get("texto") if isinstance(campo, dict) else campo
    return minimizar(texto.strip()).texto if isinstance(texto, str) and texto.strip() else ""


def _referencias(texto: str) -> tuple[set[tuple[str, str | None]], set[str]]:
    return set(mencoes(texto)), normas_mencionadas(texto)


def _numeros(texto: str) -> list[str]:
    """Numeros do texto, sem separadores ("R$ 180.000,00" -> "18000000"), em ordem."""
    return sorted(re.sub(r"\D", "", n) for n in RE_NUMERO.findall(texto or ""))


def _motivo_descarte(original: str, sugerido: str) -> str | None:
    if _referencias(sugerido) != _referencias(original):
        return "a sugestão alterava menções a artigos ou normas"
    if _numeros(sugerido) != _numeros(original):
        return "a sugestão alterava números, valores ou datas"
    if len(sugerido) > CRESCIMENTO_MAXIMO * len(original) + FOLGA_CARACTERES:
        return "a sugestão acrescentava conteúdo ao texto"
    return None


def provedor_correcao() -> ProvedorLLM:
    """Provedor do revisor: LLM_PROVEDOR_CORRECAO ou, se vazio, o da geracao.

    LLM_MODELO_CORRECAO permite um modelo menor so para a revisao — no Gemini
    gratuito, a cota diaria e por modelo, e a revisao nao consome a do gerador.
    """
    nome = config.LLM_PROVEDOR_CORRECAO or None
    if (nome or config.LLM_PROVEDOR) == "echo":
        return obter_provedor(nome)
    return obter_provedor(nome, modelo=config.LLM_MODELO_CORRECAO or None)


def sugerir(conteudo: dict[str, Any]) -> SugestaoCorrecao:
    """Pede ao revisor a correcao dos campos redigidos e aplica as salvaguardas.

    Raises:
        ErroProvedorLLM: indisponibilidade do provedor ou resposta fora do esquema.
    """
    originais = {nome: _texto_do_campo(conteudo, nome) for nome in CAMPOS_REDIGIDOS}
    provedor = provedor_correcao()

    if provedor.nome == "echo":
        # O gerador deterministico nao revisa texto: devolve o original intacto.
        return SugestaoCorrecao(
            campos=[CampoCorrigido(n, t, t) for n, t in originais.items()],
            observacoes=["O provedor echo não revisa texto; configure LLM_PROVEDOR_CORRECAO."],
            provedor=provedor.nome,
            modelo="echo-deterministico",
        )

    a_revisar = {nome: texto for nome, texto in originais.items() if texto}
    if not a_revisar:
        return SugestaoCorrecao(
            campos=[CampoCorrigido(n, t, t) for n, t in originais.items()],
            observacoes=["Não há texto nos campos redigidos para revisar."],
            provedor=provedor.nome,
            modelo="",
        )

    prompt_usuario = (
        f"CAMPOS:\n{json.dumps(a_revisar, ensure_ascii=False, indent=2)}\n\n"
        f"FORMATO DA RESPOSTA (JSON):\n"
        f"{json.dumps(MODELO_SAIDA, ensure_ascii=False, indent=2)}"
    )
    inicio = time.perf_counter()
    resposta = provedor.completar(PROMPT_SISTEMA, prompt_usuario)
    try:
        dados = resposta.como_json()
    except RespostaInvalida as erro:
        raise ErroProvedorLLM(f"saida do revisor fora do esquema: {erro}") from erro
    tempo_ms = round((time.perf_counter() - inicio) * 1000, 2)

    sugeridos = dados.get("campos") if isinstance(dados.get("campos"), dict) else dados
    campos: list[CampoCorrigido] = []
    for nome, original in originais.items():
        bruto = sugeridos.get(nome) if nome in a_revisar else None
        if isinstance(bruto, dict):
            bruto = bruto.get("texto")
        sugerido = minimizar(bruto.strip()).texto if isinstance(bruto, str) else ""
        if not sugerido:
            campos.append(CampoCorrigido(nome, original, original))
            continue
        if motivo := _motivo_descarte(original, sugerido):
            log.info("Correcao do campo %s descartada: %s", nome, motivo)
            campos.append(CampoCorrigido(nome, original, original, motivo))
            continue
        campos.append(CampoCorrigido(nome, original, sugerido))

    observacoes = dados.get("observacoes")
    observacoes = [
        minimizar(str(o)).texto for o in observacoes if str(o).strip()
    ][:10] if isinstance(observacoes, list) else []

    return SugestaoCorrecao(
        campos=campos,
        observacoes=observacoes,
        provedor=provedor.nome,
        modelo=resposta.modelo,
        parametros={**resposta.parametros, "versao_prompt": VERSAO, "tempo_ms": tempo_ms},
    )


__all__ = ["sugerir", "provedor_correcao", "SugestaoCorrecao", "CampoCorrigido", "VERSAO"]
