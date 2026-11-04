"""Analise de conformidade da minuta com o art. 8º do Decreto nº 10.947/2022.

Complementa a verificacao (RN02), que responde "o modelo inventou algo?", com a
pergunta do agente publico: "o documento esta pronto para ser aprovado?". Para
cada campo do art. 8º devolve uma situacao (ok, alerta ou pendente) e uma
mensagem objetiva; no conjunto, devolve indicadores de rastreabilidade e um
indice de prontidao.

A analise e deterministica, roda em codigo e nunca altera a minuta: ela orienta a
revisao humana, sem substituir a decisao (RN04).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .verificacao import CAMPOS_EXTRAIDOS, CAMPOS_REDIGIDOS, normalizar, verificar

OK, ALERTA, PENDENTE = "ok", "alerta", "pendente"

TAMANHO_MINIMO = {"justificativa_necessidade": 120, "descricao_objeto": 40}
PRIORIDADES = {"baixo", "baixa", "medio", "media", "alto", "alta"}
RE_DATA = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b")
RE_MES_ANO = re.compile(r"\b(\d{1,2})[/.-](\d{4})\b")
RE_NUMERO = re.compile(r"\d")


def _item(campo: str, situacao: str, mensagem: str) -> dict[str, str]:
    return {"campo": campo, "situacao": situacao, "mensagem": mensagem}


def _data(texto: str) -> date | None:
    """Interpreta dd/mm/aaaa ou mm/aaaa; None se nao reconhecer."""
    if m := RE_DATA.search(texto):
        dia, mes, ano = (int(g) for g in m.groups())
        ano += 2000 if ano < 100 else 0
        try:
            return date(ano, mes, dia)
        except ValueError:
            return None
    if m := RE_MES_ANO.search(texto):
        mes, ano = (int(g) for g in m.groups())
        try:
            return date(ano, mes, 1)
        except ValueError:
            return None
    return None


def _redigido(nome: str, campo: dict[str, Any], problema: dict[str, Any]) -> dict[str, str]:
    texto = (campo.get("texto") or "").strip()
    if not texto:
        return _item(nome, PENDENTE, "Campo obrigatório sem texto.")
    if problema.get("fundamentos_invalidos"):
        return _item(
            nome, ALERTA,
            "Cita fundamento inexistente no contexto: "
            + ", ".join(problema["fundamentos_invalidos"]) + ".",
        )
    if problema.get("artigos_fora_do_contexto"):
        return _item(
            nome, ALERTA,
            "Menciona artigo fora dos trechos recuperados: art. "
            + ", art. ".join(problema["artigos_fora_do_contexto"]) + ".",
        )
    if problema.get("normas_fora_do_contexto"):
        return _item(
            nome, ALERTA,
            "Cita norma ausente dos trechos recuperados (nº "
            + ", nº ".join(problema["normas_fora_do_contexto"])
            + "); confira se está vigente.",
        )
    if problema.get("sem_fundamento") or not campo.get("fundamentos"):
        return _item(nome, ALERTA, "Texto sem fundamento normativo vinculado.")
    if len(texto) < TAMANHO_MINIMO.get(nome, 0):
        return _item(nome, ALERTA, "Texto muito sucinto; considere detalhar.")
    return _item(nome, OK, f"Fundamentado em {', '.join(campo['fundamentos'])}.")


def _extraido(nome: str, campo: dict[str, Any], problema: dict[str, Any]) -> dict[str, str]:
    valor = (campo.get("valor") or "").strip()
    if not valor:
        if nome == "vinculacao_outro_dfd":
            return _item(nome, OK, "Sem vinculação informada (campo opcional na prática).")
        return _item(nome, PENDENTE, "Não informado na demanda; preencha se aplicável.")
    if problema.get("extracao_sem_evidencia"):
        return _item(nome, ALERTA, "Valor sem evidência literal na demanda: confira.")

    origem = "informado pelo agente" if campo.get("origem") == "agente" else "extraído da demanda"
    if nome == "grau_prioridade" and normalizar(valor) not in PRIORIDADES:
        return _item(nome, ALERTA, "Use baixo, médio ou alto (art. 8º, VI).")
    if nome in {"quantidade", "estimativa_valor"} and not RE_NUMERO.search(valor):
        return _item(nome, ALERTA, "Valor sem número; informe a quantidade ou o montante.")
    if nome == "data_pretendida":
        quando = _data(valor)
        if quando is None:
            return _item(nome, ALERTA, "Data não reconhecida; use dd/mm/aaaa ou mm/aaaa.")
        if quando < date.today():
            return _item(nome, ALERTA, "A data pretendida já passou.")
    return _item(nome, OK, f"Valor {origem}.")


def analisar(
    conteudo: dict[str, Any],
    fontes: list[dict[str, Any]],
    descricao: str,
    *,
    verificacao: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produz o checklist do art. 8º e os indicadores da minuta.

    Args:
        conteudo: minuta no esquema canonico.
        fontes: trechos vinculados, na ordem T1..Tn.
        descricao: demanda (ja minimizada).
        verificacao: relatorio ja calculado; recalculado se ausente.
    """
    if verificacao is None:
        verificacao = verificar(conteudo, fontes, descricao).para_dict()
    por_campo = verificacao.get("por_campo", {})

    checklist: list[dict[str, str]] = []
    for nome in CAMPOS_REDIGIDOS:
        campo = conteudo.get(nome) if isinstance(conteudo.get(nome), dict) else {}
        checklist.append(_redigido(nome, campo, por_campo.get(nome, {})))
    for nome in CAMPOS_EXTRAIDOS:
        campo = conteudo.get(nome) if isinstance(conteudo.get(nome), dict) else {}
        checklist.append(_extraido(nome, campo, por_campo.get(nome, {})))
    checklist.append(
        _item("area_requisitante", OK, "Preenchido a partir do cadastro do requisitante.")
    )

    contagem = {s: sum(1 for c in checklist if c["situacao"] == s) for s in (OK, ALERTA, PENDENTE)}
    similaridades = [float(f.get("similaridade") or 0.0) for f in fontes]
    citados = {
        f
        for nome in CAMPOS_REDIGIDOS
        for f in (conteudo.get(nome) or {}).get("fundamentos", [])
    }
    normas = {
        (f.get("referencia") or "").split(",")[0].strip()
        for f in fontes
        if f.get("marcador") in citados
    }
    redigidos_ok = all(
        c["situacao"] == OK for c in checklist if c["campo"] in CAMPOS_REDIGIDOS
    )

    return {
        "checklist": checklist,
        "resumo": contagem,
        "prontidao": round(contagem[OK] / len(checklist), 4),
        "pronto_para_validacao": redigidos_ok and contagem[ALERTA] == 0,
        "indicadores": {
            "taxa_ancoragem": verificacao.get("taxa_ancoragem"),
            "citacoes_invalidas": verificacao.get("citacoes_invalidas", 0),
            "fontes_recuperadas": len(fontes),
            "fontes_citadas": len(citados),
            "normas_citadas": sorted(n for n in normas if n),
            "similaridade_media": round(sum(similaridades) / len(similaridades), 4)
            if similaridades else None,
            "similaridade_maxima": round(max(similaridades), 4) if similaridades else None,
        },
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
    }


__all__ = ["analisar", "OK", "ALERTA", "PENDENTE"]
