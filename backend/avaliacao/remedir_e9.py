"""Reaplica a medida corrigida do E9 (versao 0.4.5) as geracoes ja gravadas.

Ate a versao 0.4.4, as citacoes das condicoes do sistema eram contadas so no
texto dos campos redigidos. O sistema, porem, nao escreve as citacoes no texto:
o modelo indica os marcadores dos trechos que fundamentam cada campo, e o
documento exportado os apresenta na linha "Fundamentacao". A contagem antiga
deixava, assim, quase todas as citacoes do sistema fora da medida. A geracao
nao muda; muda apenas o texto sobre o qual as citacoes sao contadas.

Os checkpoints do E9 guardam o texto dos campos, mas nao os marcadores. Os
marcadores estao nos bancos temporarios das execucoes, de onde o roteiro
rodada_final/extrair_minutas_e9.py os copia para um arquivo JSONL. Este modulo
associa cada linha do checkpoint a sua minuta (mesma demanda e mesmo texto dos
campos), recalcula as metricas de citacao sobre o texto do documento e regrava
o checkpoint, com uma copia do original. Nenhum modelo de linguagem e chamado.

A conferencia manual das citacoes que a medida automatica classificou como
"artigo inexistente" fica em avaliacao/dados/auditoria_e9.yaml; as entradas
conferidas como nao inexistentes sao descontadas da contagem, e a linha guarda a
contagem automatica original (inexistentes_automaticas) e as entradas aplicadas.

Uso (na pasta backend):
    python -m avaliacao.remedir_e9 --minutas ../rodada_final/e9_minutas/minutas_e9.jsonl
Depois, refaca as tabelas sem novas geracoes:
    python -m avaliacao.executar --somente-e9 --llm groq
    python -m avaliacao.executar --somente-e9 --llm gemini --amostra 6
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .corpus import artigo_de, dispositivos, gabarito
from .experimentos import (
    CONDICOES_E9,
    _citacoes_e9,

    documento_e9,
    medir_citacoes_sistema,
)

PASTA_E9 = Path(__file__).resolve().parent / "resultados" / "e9"
AUDITORIA = Path(__file__).resolve().parent / "dados" / "auditoria_e9.yaml"


def _texto_campos(conteudo: dict[str, Any], campos: dict[str, Any]) -> str:
    """O mesmo texto que o checkpoint guarda em "texto" (chave da associacao)."""
    return "\n".join(
        [(conteudo.get(c) or {}).get("texto") or ""
         for c in ("justificativa_necessidade", "descricao_objeto")]
        + [str((conteudo.get(c) or {}).get("valor") or "") for c in campos]
    )


def carregar_auditoria(arquivo: Path = AUDITORIA) -> dict[str, dict[tuple[str, str], list[dict]]]:
    """execucao -> (id, condicao) -> entradas da conferencia manual."""
    if not arquivo.exists():
        return {}
    import yaml

    bruto = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
    saida: dict[str, dict[tuple[str, str], list[dict]]] = {}
    for execucao, entradas in bruto.items():
        for e in entradas or []:
            saida.setdefault(execucao, {}).setdefault((e["id"], e["condicao"]), []).append(e)
    return saida


def inexistentes_detalhe(texto: str, existentes: set[tuple[str, str]]) -> list[str]:
    """Artigos que a medida automatica classifica como inexistentes (com repeticao)."""
    from app.core.verificacao import mencoes

    from .experimentos import _NORMAS_E9

    saida = []
    for artigo, norma in mencoes(texto):
        artigo = artigo.split("-")[0]
        siglas = ([_NORMAS_E9[norma]] if norma in _NORMAS_E9
                  else ([] if norma else list(_NORMAS_E9.values())))
        if siglas and not any((s, artigo) in existentes for s in siglas):
            saida.append(artigo)
    return saida


def aplicar_auditoria(ln: dict[str, Any], entradas: list[dict], texto: str,
                      existentes: set[tuple[str, str]]) -> None:
    """Desconta as citacoes conferidas como nao inexistentes; confere cada entrada."""
    if not entradas:
        return
    apontados = Counter(inexistentes_detalhe(texto, existentes))
    for e in entradas:
        if apontados[str(e["artigo"])] < int(e.get("vezes", 1)):
            raise ValueError(f"auditoria: art. {e['artigo']} de {ln['id']}/{ln['condicao']} "
                             "nao esta entre as citacoes apontadas como inexistentes")
    descontar = sum(int(e.get("vezes", 1)) for e in entradas)
    automaticas = ln.get("inexistentes_automaticas", ln["citacoes_inexistentes"])
    ln["inexistentes_automaticas"] = automaticas
    ln["citacoes_inexistentes"] = automaticas - descontar
    ln["auditoria"] = [{"artigo": str(e["artigo"]), "vezes": int(e.get("vezes", 1)),
                        "classificacao": e["classificacao"]} for e in entradas]


def _demandas() -> dict[str, dict[str, Any]]:
    g = gabarito()
    saida = {d["id"]: d for d in g["demandas"]}
    for d in g.get("adversariais", []):
        saida[d["id"]] = dict(d, campos={}, relevantes=[])
    return saida


def _existentes() -> set[tuple[str, str]]:
    return {(sig, artigo_de(f"{sig}|{d.referencia}").split("|")[1].replace("º", ""))
            for sig, lista in dispositivos().items() for d in lista}


def indexar_minutas(arquivo: Path, demandas: dict[str, dict[str, Any]]
                    ) -> dict[tuple[str, str], dict[str, Any]]:
    """(id da demanda, texto dos campos) -> minuta extraida; a mais recente prevalece."""
    por_descricao = {d["descricao"].strip(): d for d in demandas.values()}
    indice: dict[tuple[str, str], dict[str, Any]] = {}
    for bruta in arquivo.read_text(encoding="utf-8").splitlines():
        if not bruta.strip():
            continue
        m = json.loads(bruta)
        d = por_descricao.get(str(m.get("descricao", "")).strip())
        if not d:
            continue
        chave = (d["id"], _texto_campos(m.get("conteudo") or {}, d.get("campos") or {}))
        if chave not in indice or str(m.get("data_geracao")) >= str(indice[chave].get("data_geracao")):
            indice[chave] = m
    return indice


def remedir_linha(ln: dict[str, Any], minuta: dict[str, Any], d: dict[str, Any],
                  existentes: set[tuple[str, str]]) -> dict[str, Any]:
    """Recalcula, sobre o texto do documento, as metricas de citacao de uma linha."""
    relevantes = {tuple(artigo_de(r).replace("º", "").split("|")) for r in d.get("relevantes", [])}
    referencias = {v["marcador"]: v["referencia"] for v in minuta.get("vinculos", [])}
    documento, invalidos, fundamentacao = documento_e9(
        minuta.get("conteudo") or {}, referencias, d.get("campos") or {})
    nova = dict(ln)
    for chave in ("citacoes", "citacoes_inexistentes", "citacoes_pertinentes",
                  "citacoes_norma_revogada", "menciona_norma_revogada"):
        nova.pop(chave, None)
    nova["fundamentacao"] = fundamentacao
    medir_citacoes_sistema(nova, documento, invalidos, existentes, relevantes)
    nova["medida"] = "documento (0.4.5)"
    return nova


def remedir_arquivo(checkpoint: Path, indice: dict[tuple[str, str], dict[str, Any]],
                    demandas: dict[str, dict[str, Any]], existentes: set[tuple[str, str]],
                    auditoria: dict[tuple[str, str], list[dict]] | None = None) -> Counter:
    """Regrava o checkpoint com a medida corrigida; guarda o original uma unica vez."""
    linhas: dict[tuple[str, str], dict[str, Any]] = {}
    for bruta in checkpoint.read_text(encoding="utf-8").splitlines():
        if bruta.strip():
            ln = json.loads(bruta)
            linhas[(ln["id"], ln["condicao"])] = ln   # a ultima tentativa prevalece
    contagem: Counter = Counter()
    saida = []
    for (ident, condicao), ln in linhas.items():
        sistema = CONDICOES_E9.get(condicao, ("", None))[1] is not None
        if ln.get("status") != 200 or not sistema:
            if ln.get("status") == 200 and ln.get("texto") is not None:
                d = demandas.get(ident, {})
                rel = {tuple(artigo_de(r).replace("º", "").split("|")) for r in d.get("relevantes", [])}
                ln.pop("inexistentes_automaticas", None)
                ln.pop("auditoria", None)
                ln.update(_citacoes_e9(ln["texto"], existentes, rel))   # isolada: inalterada
                entradas = (auditoria or {}).get((ident, condicao), [])
                aplicar_auditoria(ln, entradas, ln["texto"], existentes)
                contagem["auditadas"] += bool(entradas)
            contagem["sem_alteracao"] += 1
            saida.append(ln)
            continue
        minuta = indice.get((ident, ln.get("texto")))
        if minuta is None:
            contagem["minuta_nao_encontrada"] += 1
            ln["medida"] = "texto dos campos (minuta nao encontrada)"
            saida.append(ln)
            continue
        saida.append(remedir_linha(ln, minuta, demandas[ident], existentes))
        contagem["remedidas"] += 1
    original = checkpoint.with_name(checkpoint.stem + ".antes_remedicao.jsonl")
    if not original.exists():
        shutil.copy2(checkpoint, original)
    checkpoint.write_text("".join(json.dumps(x, ensure_ascii=False, default=str) + "\n"
                                  for x in saida), encoding="utf-8")
    return contagem


def main() -> None:
    analisador = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    analisador.add_argument("--minutas", type=Path, required=True,
                            help="JSONL gravado por rodada_final/extrair_minutas_e9.py")
    analisador.add_argument("--pasta", type=Path, default=PASTA_E9,
                            help="pasta dos checkpoints do E9")
    args = analisador.parse_args()
    demandas = _demandas()
    existentes = _existentes()
    indice = indexar_minutas(args.minutas, demandas)
    auditoria = carregar_auditoria()
    print(f"Minutas extraidas e associaveis: {len(indice)}")
    falhas = 0
    for checkpoint in sorted(args.pasta.glob("*.jsonl")):
        if checkpoint.name.endswith(".antes_remedicao.jsonl"):
            continue
        contagem = remedir_arquivo(checkpoint, indice, demandas, existentes,
                                   auditoria.get(checkpoint.stem, {}))
        falhas += contagem["minuta_nao_encontrada"]
        print(f"  {checkpoint.name}: {dict(contagem)}")
    if falhas:
        raise SystemExit(f"{falhas} linha(s) sem minuta correspondente: extraia os bancos de novo")


if __name__ == "__main__":
    main()
