"""Progresso do E9 a partir dos checkpoints (backend/avaliacao/resultados/e9/*.jsonl).

Só usa a biblioteca padrão. Não altera nada: lê os arquivos e grava
rodada_final/logs/E9_progresso.md com o que foi concluído e o que falta.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
E9 = AQUI.parent / "backend" / "avaliacao" / "resultados" / "e9"
FINAIS = {200, 422}
CONDICOES = ["completo", "completo_bm25", "sem_rag", "isolada"]
DEMANDAS = [f"D{i:02d}" for i in range(1, 33)]
ESPERADO = {
    "groq-openai-gpt-oss-120b.jsonl": [(d, c) for d in DEMANDAS for c in CONDICOES],
    "groq-openai-gpt-oss-120b_injecao.jsonl": [(f"A{i:02d}", c) for i in range(1, 7) for c in ("completo", "isolada")],
    # amostra de 6 demandas espaçadas uniformemente (avaliacao.experimentos.amostra_demandas)
    "gemini-gemini-3.6-flash.jsonl": [(d, c) for d in ("D01", "D06", "D11", "D17", "D22", "D27") for c in CONDICOES],
}


def ler(arquivo: Path) -> dict[tuple[str, str], dict]:
    ultimo: dict[tuple[str, str], dict] = {}
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        if linha.strip():
            x = json.loads(linha)
            ultimo[(x["id"], x["condicao"])] = x
    return ultimo


def groq_completo() -> bool:
    for nome in ("groq-openai-gpt-oss-120b.jsonl", "groq-openai-gpt-oss-120b_injecao.jsonl"):
        arquivo = E9 / nome
        feitos = ler(arquivo) if arquivo.exists() else {}
        if any(feitos.get(k, {}).get("status") not in FINAIS for k in ESPERADO[nome]):
            return False
    return True


def main() -> None:
    import sys

    if "--groq-completo" in sys.argv:
        raise SystemExit(0 if groq_completo() else 1)
    linhas = [f"# Progresso do E9 ({datetime.now():%d/%m/%Y %H:%M})", ""]
    for nome, esperado in ESPERADO.items():
        arquivo = E9 / nome
        feitos = ler(arquivo) if arquivo.exists() else {}
        rotulo_extra = ""
        concluidos = [k for k in esperado if feitos.get(k, {}).get("status") in FINAIS]
        pendentes = [k for k in esperado if k not in concluidos]
        estados = Counter(str(v.get("status")) for v in feitos.values())
        tokens = sum((v.get("tokens_entrada") or 0) + (v.get("tokens_saida") or 0) for v in feitos.values())
        ultima = max((v.get("executado_em", "") for v in feitos.values()), default="—")
        linhas += [f"## {nome}{rotulo_extra}", "",
                   f"- Concluídas: {len(concluidos)} de {len(esperado)}; pendentes: {len(pendentes)}",
                   f"- Situações registradas: {dict(estados)}; tokens consumidos: {tokens}",
                   f"- Última gravação: {ultima}"]
        if pendentes:
            linhas.append("- Próximas a executar: " + ", ".join(f"{d}/{c}" for d, c in pendentes[:12])
                          + (" …" if len(pendentes) > 12 else ""))
        linhas.append("")
    linhas.append("Para continuar: C_E9_groq.bat (Groq) ou o comando do Gemini com --amostra 6, num novo dia.")
    destino = AQUI / "logs" / "E9_progresso.md"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print("\n".join(linhas))


if __name__ == "__main__":
    main()
