"""Selecao do provedor de modelo de linguagem (RNF08).

Unico ponto do sistema que decide qual implementacao usar. Trocar de provedor e
mudar a variavel LLM_PROVEDOR: nenhum outro modulo conhece o fornecedor.
"""
from __future__ import annotations

import logging

from ..config import config
from .base import ErroProvedorLLM, ProvedorLLM
from .provedores import (
    ProvedorAnthropic,
    ProvedorEco,
    ProvedorGemini,
    ProvedorGroq,
    ProvedorOllama,
)

log = logging.getLogger(__name__)

REGISTRO: dict[str, type[ProvedorLLM]] = {
    "anthropic": ProvedorAnthropic,
    "gemini": ProvedorGemini,
    "groq": ProvedorGroq,
    "ollama": ProvedorOllama,
    "echo": ProvedorEco,
}

_provedor: ProvedorLLM | None = None


def obter_provedor(nome: str | None = None, *, modelo: str | None = None) -> ProvedorLLM:
    """Devolve o provedor configurado.

    Args:
        nome: sobrepoe a configuracao (usado nos experimentos "nuvem x local" e
            na revisao de texto por outro fornecedor).
        modelo: sobrepoe o modelo padrao do provedor (revisao de texto).
    """
    global _provedor

    escolhido = (nome or config.LLM_PROVEDOR).lower().strip()
    if escolhido not in REGISTRO:
        raise ValueError(
            f"LLM_PROVEDOR invalido: {escolhido!r}. Use um de {sorted(REGISTRO)}."
        )

    padrao = nome is None and modelo is None
    if padrao and _provedor is not None:
        return _provedor

    try:
        instancia = REGISTRO[escolhido](**({"modelo": modelo} if modelo else {}))
    except ErroProvedorLLM:
        raise
    except Exception as erro:  # noqa: BLE001
        raise ErroProvedorLLM(f"falha ao inicializar o provedor {escolhido}: {erro}") from erro

    if padrao:
        _provedor = instancia
        log.info("Provedor de LLM ativo: %s", escolhido)
    return instancia


def redefinir_provedor(instancia: ProvedorLLM | None = None) -> None:
    """Substitui o provedor global (usado em testes)."""
    global _provedor
    _provedor = instancia


__all__ = ["obter_provedor", "redefinir_provedor", "REGISTRO"]
