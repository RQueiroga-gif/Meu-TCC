"""Verificacao automatica da saida do modelo (RN02, RNF02).

Transforma a fidelidade ao contexto em metrica mensuravel. O modelo exerce dois
papeis distintos, e cada um tem sua verificacao:

  * Campos REDIGIDOS (art. 8º, I e II do Decreto nº 10.947/2022): o modelo declara
    em "fundamentos" os identificadores (T1, T2...) dos trechos que sustentam o
    texto. Verifica-se se cada identificador existe no contexto e se nenhum artigo
    mencionado no corpo do texto esta fora dos trechos recuperados.

  * Campos EXTRAIDOS (art. 8º, III a VII): o modelo copia em "evidencia" o trecho
    literal da demanda que sustenta o valor. Verifica-se se essa evidencia aparece
    de fato na demanda — e o que impede quantidades, valores e datas inventados,
    principal fonte de alucinacao factual em documento administrativo.

A verificacao e deterministica e roda em codigo, nao no modelo.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

RE_MENCAO_ARTIGO = re.compile(r"art(?:igo)?\.?\s*(\d+(?:-[A-Za-z])?)", re.I)

CAMPOS_REDIGIDOS = ("justificativa_necessidade", "descricao_objeto")
CAMPOS_EXTRAIDOS = (
    "quantidade",
    "estimativa_valor",
    "data_pretendida",
    "grau_prioridade",
    "vinculacao_outro_dfd",
)


def normalizar(texto: str | None) -> str:
    """Minusculas, sem acento e com espacos colapsados, para comparacao textual."""
    if not texto:
        return ""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


@dataclass(slots=True)
class ProblemaCampo:
    campo: str
    fundamentos_invalidos: list[str] = field(default_factory=list)
    artigos_fora_do_contexto: list[str] = field(default_factory=list)
    sem_fundamento: bool = False
    extracao_sem_evidencia: bool = False

    @property
    def tem_problema(self) -> bool:
        return bool(
            self.fundamentos_invalidos
            or self.artigos_fora_do_contexto
            or self.sem_fundamento
            or self.extracao_sem_evidencia
        )

    def para_dict(self) -> dict[str, Any]:
        return {
            "fundamentos_invalidos": self.fundamentos_invalidos,
            "artigos_fora_do_contexto": self.artigos_fora_do_contexto,
            "sem_fundamento": self.sem_fundamento,
            "extracao_sem_evidencia": self.extracao_sem_evidencia,
        }


@dataclass(slots=True)
class RelatorioVerificacao:
    taxa_ancoragem: float | None
    total_citacoes: int
    citacoes_invalidas: int
    inconsistencia_detectada: bool
    por_campo: dict[str, dict[str, Any]]

    def para_dict(self) -> dict[str, Any]:
        return {
            "taxa_ancoragem": self.taxa_ancoragem,
            "total_citacoes": self.total_citacoes,
            "citacoes_invalidas": self.citacoes_invalidas,
            "inconsistencia_detectada": self.inconsistencia_detectada,
            "por_campo": self.por_campo,
        }


def verificar(
    minuta: dict[str, Any],
    contexto: list[dict[str, Any]],
    demanda: str,
) -> RelatorioVerificacao:
    """Confere a minuta gerada contra o contexto recuperado e a demanda.

    Args:
        minuta: saida estruturada do modelo.
        contexto: trechos recuperados, na ordem em que foram numerados T1..Tn.
        demanda: descricao (ja minimizada) informada pelo agente publico.
    """
    ids_validos = {f"T{n}" for n in range(1, len(contexto) + 1)}
    artigos_contexto: set[str] = set()
    for trecho in contexto:
        alvo = f"{trecho.get('referencia', '')} {trecho.get('texto', '')}"
        artigos_contexto.update(m.lower() for m in RE_MENCAO_ARTIGO.findall(alvo))

    demanda_normalizada = normalizar(demanda)
    problemas: dict[str, ProblemaCampo] = {}
    redigidos = ancorados = total_citacoes = citacoes_invalidas = 0

    for nome, campo in minuta.items():
        if not isinstance(campo, dict):
            continue
        problema = ProblemaCampo(campo=nome)

        # ------------------------------------------------ campo redigido
        if "texto" in campo:
            redigidos += 1
            fundamentos = campo.get("fundamentos") or []
            total_citacoes += len(fundamentos)
            problema.fundamentos_invalidos = [f for f in fundamentos if f not in ids_validos]
            citacoes_invalidas += len(problema.fundamentos_invalidos)
            problema.sem_fundamento = not fundamentos
            mencionados = {m.lower() for m in RE_MENCAO_ARTIGO.findall(campo.get("texto") or "")}
            problema.artigos_fora_do_contexto = sorted(mencionados - artigos_contexto)
            if fundamentos and not problema.fundamentos_invalidos:
                ancorados += 1

        # ------------------------------------------------ campo extraido
        elif campo.get("valor") is not None:
            if campo.get("origem") == "agente":
                # Informacao declarada pelo agente publico na revisao: nao e
                # saida do modelo, logo nao ha alucinacao a verificar.
                continue
            evidencia = normalizar(campo.get("evidencia"))
            problema.extracao_sem_evidencia = not (
                evidencia and evidencia in demanda_normalizada
            )

        else:
            continue   # campo pendente (valor nulo): nada a verificar

        problemas[nome] = problema

    return RelatorioVerificacao(
        taxa_ancoragem=round(ancorados / redigidos, 4) if redigidos else None,
        total_citacoes=total_citacoes,
        citacoes_invalidas=citacoes_invalidas,
        inconsistencia_detectada=any(p.tem_problema for p in problemas.values()),
        por_campo={nome: p.para_dict() for nome, p in problemas.items()},
    )


def pendencias(minuta: dict[str, Any]) -> list[str]:
    """Campos do art. 8º sem valor extraido da demanda."""
    return [
        nome
        for nome in CAMPOS_EXTRAIDOS
        if isinstance(minuta.get(nome), dict) and minuta[nome].get("valor") is None
    ]


__all__ = [
    "verificar",
    "pendencias",
    "normalizar",
    "RelatorioVerificacao",
    "ProblemaCampo",
    "CAMPOS_REDIGIDOS",
    "CAMPOS_EXTRAIDOS",
]
