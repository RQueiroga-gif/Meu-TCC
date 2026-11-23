"""Copia, dos bancos temporarios do roteiro de avaliacao, as minutas geradas no E9.

Cada execucao de `python -m avaliacao.executar` cria uma pasta temporaria
`dfd-avaliacao-*` com o banco SQLite usado naquela execucao, e a pasta nao e
apagada. As minutas do E9 (modelos reais) ficam nesses bancos com o conteudo
completo, inclusive os marcadores de fundamento de cada campo (T1..T6), que o
checkpoint do E9 nao guarda. Este roteiro so le os bancos (modo somente leitura)
e grava um arquivo JSONL com uma linha por minuta. Usa apenas a biblioteca
padrao. Nao le nem grava chaves de API.

Uso: python extrair_minutas_e9.py [pasta_temporaria]
Saida: rodada_final/e9_minutas/minutas_e9.jsonl e resumo.txt
Escrito pelo Claude (rodada final, 23/11/2026).
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SAIDA = AQUI / "e9_minutas"

CONSULTA_MINUTAS = """
SELECT m.id_minuta, m.conteudo, m.modelo_llm, m.versao_prompt, m.parametros,
       m.data_geracao, d.titulo, d.descricao
  FROM minuta m
  JOIN documento_dfd f ON f.id_dfd = m.id_dfd
  JOIN demanda d ON d.id_demanda = f.id_demanda
 WHERE m.origem = 'geracao'
   AND (m.modelo_llm LIKE '%gpt-oss%' OR m.modelo_llm LIKE '%gemini%')
"""
CONSULTA_VINCULOS = """
SELECT mt.ordem, n.tipo, n.numero, n.ano, t.dispositivo, n.sigla, t.artigo
  FROM minuta_trecho mt
  JOIN trecho_normativo t ON t.id_trecho = mt.id_trecho
  JOIN norma n ON n.id_norma = t.id_norma
 WHERE mt.id_minuta = ?
 ORDER BY mt.ordem
"""


def _json(valor):
    if valor is None or isinstance(valor, (dict, list)):
        return valor
    try:
        return json.loads(valor)
    except (TypeError, ValueError):
        return valor


def main() -> None:
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir())
    bancos = sorted(glob.glob(str(base / "dfd-avaliacao-*" / "avaliacao.sqlite3")))
    SAIDA.mkdir(exist_ok=True)
    total = 0
    linhas_resumo = [f"Pasta temporaria: {base}", f"Bancos encontrados: {len(bancos)}"]
    with (SAIDA / "minutas_e9.jsonl").open("w", encoding="utf-8") as saida:
        for banco in bancos:
            try:
                con = sqlite3.connect(f"file:{banco}?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
                minutas = con.execute(CONSULTA_MINUTAS).fetchall()
            except sqlite3.Error as erro:
                linhas_resumo.append(f"  {Path(banco).parent.name}: erro {erro}")
                continue
            for m in minutas:
                vinculos = [
                    {"marcador": f"T{v['ordem']}",
                     "referencia": f"{v['tipo']} nº {v['numero']}/{v['ano']}, {v['dispositivo']}",
                     "sigla": v["sigla"], "artigo": v["artigo"]}
                    for v in con.execute(CONSULTA_VINCULOS, (m["id_minuta"],)).fetchall()
                ]
                parametros = _json(m["parametros"]) or {}
                saida.write(json.dumps({
                    "banco": Path(banco).parent.name,
                    "id_minuta": m["id_minuta"],
                    "modelo": m["modelo_llm"],
                    "versao_prompt": m["versao_prompt"],
                    "usar_rag": parametros.get("usar_rag"),
                    "tokens_entrada": parametros.get("tokens_entrada"),
                    "tokens_saida": parametros.get("tokens_saida"),
                    "data_geracao": str(m["data_geracao"]),
                    "titulo": m["titulo"],
                    "descricao": m["descricao"],
                    "conteudo": _json(m["conteudo"]),
                    "vinculos": vinculos,
                }, ensure_ascii=False) + "\n")
                total += 1
            linhas_resumo.append(f"  {Path(banco).parent.name}: {len(minutas)} minutas do E9")
            con.close()
    linhas_resumo.append(f"Total: {total} minutas")
    (SAIDA / "resumo.txt").write_text("\n".join(linhas_resumo) + "\n", encoding="utf-8")
    print("\n".join(linhas_resumo))


if __name__ == "__main__":
    main()
