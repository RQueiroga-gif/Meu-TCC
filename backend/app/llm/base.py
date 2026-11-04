"""Contrato do provedor de modelo de linguagem.

Realiza a interface «interface» ProvedorLLM do diagrama de classes (Figura 6) e
atende ao RNF08 (manutenibilidade): trocar o provedor e trocar uma implementacao,
sem efeito sobre prompt, esquema de saida, orquestracao ou persistencia.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

RE_BLOCO_JSON = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


class ErroProvedorLLM(RuntimeError):
    """Falha ao obter resposta do modelo (fluxo alternativo A2)."""


class RespostaInvalida(ValueError):
    """O modelo respondeu, mas fora do esquema esperado."""


@dataclass(slots=True)
class Resposta:
    """Resposta bruta do provedor, com os metadados de reprodutibilidade."""

    texto: str
    modelo: str
    parametros: dict[str, Any]

    def como_json(self) -> dict[str, Any]:
        """Extrai o objeto JSON da resposta, tolerando cercas de codigo."""
        bruto = self.texto.strip()
        if bloco := RE_BLOCO_JSON.search(bruto):
            bruto = bloco.group(1).strip()
        if not bruto.startswith("{"):
            inicio, fim = bruto.find("{"), bruto.rfind("}")
            if inicio == -1 or fim <= inicio:
                raise RespostaInvalida("resposta do modelo nao contem objeto JSON")
            bruto = bruto[inicio : fim + 1]
        try:
            return json.loads(bruto)
        except json.JSONDecodeError as erro:
            raise RespostaInvalida(f"JSON invalido na resposta do modelo: {erro}") from erro


class ProvedorLLM(ABC):
    """Interface que todo provedor deve implementar."""

    nome: str = "abstrato"

    @abstractmethod
    def completar(
        self, prompt_sistema: str, prompt_usuario: str, *, formato_json: bool = True
    ) -> Resposta:
        """Envia o prompt e devolve a resposta do modelo.

        Args:
            formato_json: pede ao provedor saida em JSON (quando ele oferece o
                recurso). False so na condicao "LLM isolada" do Experimento 9,
                em que o modelo responde em texto livre.

        Raises:
            ErroProvedorLLM: indisponibilidade, timeout ou erro de autenticacao.
        """

    def descrever(self) -> dict[str, Any]:
        return {"provedor": self.nome}


__all__ = [
    "ProvedorLLM",
    "Resposta",
    "ErroProvedorLLM",
    "RespostaInvalida",
]
