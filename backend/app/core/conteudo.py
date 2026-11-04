"""Normalizacao do conteudo da minuta (esquema do art. 8º, RN06).

Todo conteudo que vira versao de minuta passa por aqui, venha do modelo de
linguagem ou da edicao do agente publico:

  * o formato e garantido: os sete campos existem, os redigidos tem "texto" e
    "fundamentos" (lista de marcadores), os extraidos tem "valor" e "evidencia";
    modelos pequenos (Ollama) frequentemente devolvem listas como texto, campos
    ausentes ou chaves extras;
  * a minimizacao de dados pessoais (RN06) e reaplicada: o agente pode digitar
    um CPF ou e-mail durante a revisao;
  * a origem de cada valor extraido e registrada: quando o agente altera um valor,
    ele passa a ser "informado pelo agente" e a evidencia do modelo e descartada,
    pois ja nao sustenta o novo valor. A verificacao nao cobra evidencia de
    informacao declarada por pessoa — so de informacao produzida pelo modelo.
"""
from __future__ import annotations

import re
from typing import Any

from .anonimizador import minimizar
from .verificacao import CAMPOS_EXTRAIDOS, CAMPOS_REDIGIDOS

ORIGEM_MODELO = "modelo"
ORIGEM_AGENTE = "agente"

RE_MARCADOR = re.compile(r"T\d+", re.I)
TAMANHO_MAXIMO = 20_000


def _texto(valor: Any) -> str | None:
    """Converte para texto limpo e minimizado; vazio vira None."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        valor = str(valor)
    if not isinstance(valor, str):
        return None
    limpo = valor.strip()[:TAMANHO_MAXIMO]
    if not limpo or limpo.lower() in {"null", "none", "n/a", "-"}:
        return None
    return minimizar(limpo).texto


def _marcadores(fundamentos: Any) -> list[str]:
    """Aceita lista, texto "T1, T2" ou valor unico; devolve ["T1", "T2"]."""
    if fundamentos is None:
        return []
    if isinstance(fundamentos, str):
        itens = RE_MARCADOR.findall(fundamentos)
    elif isinstance(fundamentos, (list, tuple)):
        itens = [str(f).strip() for f in fundamentos if str(f).strip()]
    else:
        itens = [str(fundamentos)]
    vistos: list[str] = []
    for item in itens:
        marcador = item.upper()
        if marcador not in vistos:
            vistos.append(marcador)
    return vistos


def normalizar_conteudo(
    bruto: dict[str, Any] | None,
    *,
    anterior: dict[str, Any] | None = None,
    origem: str = ORIGEM_MODELO,
) -> dict[str, Any]:
    """Devolve o conteudo no esquema canonico da minuta.

    Args:
        bruto: conteudo recebido (saida do modelo ou edicao do agente).
        anterior: conteudo da versao anterior, para detectar valores alterados.
        origem: quem produziu `bruto` ("modelo" na geracao, "agente" na revisao).
    """
    bruto = bruto if isinstance(bruto, dict) else {}
    anterior = anterior if isinstance(anterior, dict) else {}
    saida: dict[str, Any] = {}

    for nome in CAMPOS_REDIGIDOS:
        campo = bruto.get(nome)
        if isinstance(campo, str):          # modelo devolveu so o texto
            campo = {"texto": campo, "fundamentos": []}
        campo = campo if isinstance(campo, dict) else {}
        saida[nome] = {
            "texto": _texto(campo.get("texto")) or "",
            "fundamentos": _marcadores(campo.get("fundamentos")),
        }

    for nome in CAMPOS_EXTRAIDOS:
        campo = bruto.get(nome)
        if not isinstance(campo, dict):     # valor solto, sem evidencia
            campo = {"valor": campo, "evidencia": None}
        valor = _texto(campo.get("valor"))
        evidencia = _texto(campo.get("evidencia"))
        origem_campo = campo.get("origem") if campo.get("origem") in {
            ORIGEM_MODELO, ORIGEM_AGENTE
        } else origem

        if origem == ORIGEM_AGENTE:
            previo = anterior.get(nome) if isinstance(anterior.get(nome), dict) else {}
            if valor != _texto(previo.get("valor")):
                # O agente alterou (ou preencheu) o valor: a evidencia do modelo
                # ja nao o sustenta, e a informacao passa a ser declarada.
                origem_campo, evidencia = ORIGEM_AGENTE, None
            else:
                origem_campo = previo.get("origem") or ORIGEM_MODELO
                evidencia = _texto(previo.get("evidencia"))

        saida[nome] = {
            "valor": valor,
            "evidencia": evidencia if valor is not None else None,
            "origem": origem_campo if valor is not None else None,
        }

    return saida


__all__ = ["normalizar_conteudo", "ORIGEM_MODELO", "ORIGEM_AGENTE"]
