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

Versao 0.3 (avaliacao de 26/10/2026, injecao de falhas): a mencao a artigo
passou a considerar a norma citada. Na versao anterior, "art. 8º do Decreto nº
10.947/2022" era aceito sempre que algum "art. 8" de qualquer norma estivesse
no contexto, e mencoes no plural ("arts. 74 e 75") nao eram reconhecidas.
Tambem passa a ser apontada a citacao de norma ausente do contexto (por
exemplo, a Lei nº 8.666/1993, revogada), erro tipico de modelos treinados em
textos anteriores a Lei nº 14.133/2021.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# "art. 18", "artigo 75", "arts. 74 e 75", "art. 6º-A" (lista de numeros apos "art.")
RE_MENCAO_ARTIGO = re.compile(
    r"\bart(?:igo)?s?\.?\s*"
    r"(\d+[º°o]?(?:\s*-\s*[A-Za-z]\b)?(?:\s*(?:,|e)\s*\d+[º°o]?(?:\s*-\s*[A-Za-z]\b)?)*)",
    re.I,
)
RE_NUMERO_ARTIGO = re.compile(r"(\d+)[º°o]?(?:\s*-\s*([A-Za-z])\b)?")
# "Lei nº 14.133/2021", "Decreto 10.947", "Instrução Normativa SEGES/ME nº 58", "LGPD"
RE_NORMA = re.compile(
    r"\b(?:(Lei(?:\s+Complementar)?|Decreto(?:-Lei)?|Instru[çc][ãa]o\s+Normativa|IN)"
    r"(?:\s+[A-Z/]{2,}(?:/[A-Z]{2,})?)?\s*(?:n[º°o.]*\s*)?(\d{1,3}(?:\.\d{3})+|\d{2,6})"
    r"|(LGPD))",
    re.I,
)
SIGLAS_NORMA = {"lgpd": "13709"}
JANELA_QUALIFICADOR = 90   # caracteres apos a mencao em que se procura "da Lei nº ..."

CAMPOS_REDIGIDOS = ("justificativa_necessidade", "descricao_objeto")
CAMPOS_EXTRAIDOS = (
    "quantidade",
    "estimativa_valor",
    "data_pretendida",
    "grau_prioridade",
    "vinculacao_outro_dfd",
)


def _artigos(lista: str) -> list[str]:
    """'74 e 75' -> ['74', '75']; '6º-A' -> ['6-a']."""
    return [
        f"{numero}-{letra.lower()}" if letra else numero
        for numero, letra in RE_NUMERO_ARTIGO.findall(lista)
    ]


def _chave_norma(achado: re.Match[str]) -> str:
    """Identificador da norma: apenas os digitos do numero ('14.133' -> '14133')."""
    if achado.group(3):
        return SIGLAS_NORMA[achado.group(3).lower()]
    return re.sub(r"\D", "", achado.group(2))


def normas_mencionadas(texto: str) -> set[str]:
    return {_chave_norma(m) for m in RE_NORMA.finditer(texto or "")}


def mencoes(texto: str) -> list[tuple[str, str | None]]:
    """Mencoes a artigo no texto, com a norma que as qualifica (ou None).

    "art. 8º do Decreto nº 10.947/2022" -> [("8", "10947")]
    "arts. 74 e 75"                       -> [("74", None), ("75", None)]
    """
    saida: list[tuple[str, str | None]] = []
    texto = texto or ""
    for achado in RE_MENCAO_ARTIGO.finditer(texto):
        resto = texto[achado.end(): achado.end() + JANELA_QUALIFICADOR]
        # o qualificador termina na proxima mencao a artigo ou no fim da frase
        corte = re.search(r"\bart(?:igo)?s?\.?\s*\d|;|\.(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]|\s*$)", resto)
        trecho = resto[: corte.start()] if corte else resto
        qualificador = RE_NORMA.search(trecho)
        norma = _chave_norma(qualificador) if qualificador else None
        saida.extend((artigo, norma) for artigo in _artigos(achado.group(1)))
    return saida


def _norma_da_referencia(referencia: str) -> str | None:
    achado = RE_NORMA.search(referencia or "")
    return _chave_norma(achado) if achado else None


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
    normas_fora_do_contexto: list[str] = field(default_factory=list)
    sem_fundamento: bool = False
    extracao_sem_evidencia: bool = False

    @property
    def tem_problema(self) -> bool:
        return bool(
            self.fundamentos_invalidos
            or self.artigos_fora_do_contexto
            or self.normas_fora_do_contexto
            or self.sem_fundamento
            or self.extracao_sem_evidencia
        )

    def para_dict(self) -> dict[str, Any]:
        return {
            "fundamentos_invalidos": self.fundamentos_invalidos,
            "artigos_fora_do_contexto": self.artigos_fora_do_contexto,
            "normas_fora_do_contexto": self.normas_fora_do_contexto,
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
    artigos_contexto: set[str] = set()            # numeros, de qualquer norma
    pares_contexto: set[tuple[str, str]] = set()  # (norma, artigo)
    normas_contexto: set[str] = set()
    for trecho in contexto:
        referencia, corpo = trecho.get("referencia", ""), trecho.get("texto", "")
        alvo = f"{referencia} {corpo}"
        propria = _norma_da_referencia(referencia)
        if propria:
            normas_contexto.add(propria)
        normas_contexto |= normas_mencionadas(alvo)
        for artigo, norma in mencoes(alvo):
            artigos_contexto.add(artigo)
            # mencao sem qualificador dentro do trecho: artigo da propria norma
            pares_contexto.add((norma or propria or "", artigo))

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
            texto = campo.get("texto") or ""
            fora: set[str] = set()
            for artigo, norma in mencoes(texto):
                if norma is None and artigo not in artigos_contexto:
                    fora.add(artigo)
                elif norma is not None and (norma, artigo) not in pares_contexto:
                    fora.add(artigo)
            problema.artigos_fora_do_contexto = sorted(fora)
            problema.normas_fora_do_contexto = sorted(
                normas_mencionadas(texto) - normas_contexto
            )
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
    "mencoes",
    "normas_mencionadas",
    "normalizar",
    "RelatorioVerificacao",
    "ProblemaCampo",
    "CAMPOS_REDIGIDOS",
    "CAMPOS_EXTRAIDOS",
]
