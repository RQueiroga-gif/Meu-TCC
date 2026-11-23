"""Executa a avaliacao completa e grava resultados, graficos e tabelas LaTeX.

Uso (na pasta backend, com o ambiente virtual ativo):

    python -m avaliacao.executar                    # E1 a E8 (sem LLM real)
    python -m avaliacao.executar --llm groq         # E1 a E8 e o E9 com a Groq
    python -m avaliacao.executar --somente-e9 --llm groq              # 32 demandas, 4 condicoes
    python -m avaliacao.executar --somente-e9 --llm gemini --amostra 6
    python -m avaliacao.executar --somente-e9 --llm groq --condicoes completo isolada
    python -m avaliacao.executar --rapido           # pula o E8 (pytest) e o E6

O E9 guarda o resultado de cada provedor/modelo separadamente (resumo.json,
chave e9.execucoes) e salva cada geracao em resultados/e9/<provedor>_<modelo>.jsonl
assim que ela fica pronta. Rodar o mesmo comando de novo continua de onde parou
(util com as cotas diarias dos niveis gratuitos da Groq e do Gemini); --refazer descarta o progresso.

O BGE-M3 entra automaticamente nos experimentos de recuperacao quando o
sentence-transformers estiver instalado (pip install sentence-transformers).

Tudo roda num banco e num indice temporarios: a base de desenvolvimento nao e
tocada. Saida em avaliacao/resultados/ (JSON, CSV, figuras e tabelas .tex).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

RAIZ_BACKEND = Path(__file__).resolve().parent.parent
SAIDA = Path(__file__).resolve().parent / "resultados"


def _ambiente_isolado() -> Path:
    """Configura banco, indice e saida temporarios ANTES de importar a aplicacao."""
    pasta = Path(tempfile.mkdtemp(prefix="dfd-avaliacao-"))
    os.environ.update({
        "DB_DIALETO": "sqlite",
        "DB_ARQUIVO": str(pasta / "avaliacao.sqlite3"),
        "INDICE_ARQUIVO": str(pasta / "indice.npz"),
        "PASTA_SAIDA": str(pasta / "saida"),
        "SECRET_KEY": "avaliacao-" + os.urandom(8).hex(),
        "LLM_PROVEDOR": "echo",
        "BCRYPT_RODADAS": "4",
        "ANONIMIZADOR_NER": os.environ.get("ANONIMIZADOR_NER", "true"),
    })
    sys.path.insert(0, str(RAIZ_BACKEND))
    return pasta


def _csv(nome: str, linhas: list[dict]) -> None:
    if not linhas:
        return
    chaves: list[str] = []
    for ln in linhas:
        chaves += [k for k in ln if k not in chaves]
    with (SAIDA / nome).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=chaves, delimiter=";")
        w.writeheader()
        w.writerows(linhas)


def _chave_execucao(provedor: str, modelo: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", f"{provedor}_{modelo}")


PACOTES_REGISTRADOS = ("fastapi", "uvicorn", "pydantic", "SQLAlchemy", "beautifulsoup4",
                       "html5lib", "numpy", "snowballstemmer", "httpx", "python-docx",
                       "sentence-transformers", "transformers", "torch", "spacy", "pytest",
                       "matplotlib", "streamlit")


def _versoes_pacotes() -> dict[str, str | None]:
    """Versoes instaladas dos pacotes da pilha (reprodutibilidade; Quadro de tecnologias)."""
    from importlib import metadata

    versoes: dict[str, str | None] = {}
    for nome in PACOTES_REGISTRADOS:
        try:
            versoes[nome] = metadata.version(nome)
        except metadata.PackageNotFoundError:
            versoes[nome] = None
    return versoes


def _e9_existente(resumo: dict) -> dict:
    """E9 ja gravado, convertido para o formato por execucao (provedor/modelo)."""
    e9 = resumo.get("e9") or {}
    if "execucoes" in e9:
        return e9
    if e9.get("provedor"):          # formato antigo: um provedor so
        return {"execucoes": {_chave_execucao(e9["provedor"], e9.get("modelo_configurado", "")): e9}}
    return {"execucoes": {}}


def main() -> int:  # noqa: C901
    analisador = argparse.ArgumentParser(description="Avaliação do Assistente de DFD")
    analisador.add_argument("--llm", nargs="+", choices=["gemini", "groq"],
                            help="executa o E9 com um ou mais provedores, em sequência")
    analisador.add_argument("--rapido", action="store_true", help="pula E6 e E8")
    analisador.add_argument(
        "--somente-e9", action="store_true",
        help="roda apenas o E9 e o acrescenta ao resumo.json existente (exige --llm)",
    )
    analisador.add_argument("--condicoes", nargs="+",
                            default=["completo", "completo_bm25", "sem_rag", "isolada"],
                            choices=["completo", "completo_bm25", "sem_rag", "isolada"],
                            help="condições do E9 (padrão: as quatro)")
    analisador.add_argument("--amostra", type=int,
                            help="E9 só com N demandas espaçadas do gabarito (cota do Gemini)")
    analisador.add_argument("--ids", nargs="+", help="E9 só com estas demandas (ex.: D01 D07)")
    analisador.add_argument("--refazer", action="store_true",
                            help="E9: descarta o progresso salvo e gera tudo de novo")
    analisador.add_argument("--sem-injecao", action="store_true",
                            help="E9: não roda as 6 demandas adversariais (injeção de prompt); "
                                 "elas já são puladas com --amostra ou --ids")
    analisador.add_argument("--cache-embeddings", action="store_true",
                            help="reaproveita os vetores do BGE-M3 já calculados (ligado no "
                                 "--somente-e9; não use quando o tempo de indexação importa)")
    args = analisador.parse_args()
    if args.somente_e9 and not args.llm:
        analisador.error("--somente-e9 exige --llm")

    pasta = _ambiente_isolado()
    from app.config import Config, config

    # A avaliacao usa os valores padrao do codigo, e nao os do .env local, para
    # que o resultado nao dependa da maquina em que roda.
    for campo in ("RECUPERACAO_LIMIAR", "RECUPERACAO_LIMIAR_HASH", "RECUPERACAO_LIMIAR_SPACY",
                  "RECUPERACAO_MODO", "RECUPERACAO_TOP_K"):
        setattr(config, campo, Config.model_fields[campo].default)

    from . import experimentos as E
    from . import relatorio as R
    from .corpus import hashes, unidades_por_dispositivo

    SAIDA.mkdir(parents=True, exist_ok=True)
    (SAIDA / "figuras").mkdir(exist_ok=True)
    (SAIDA / "latex").mkdir(exist_ok=True)
    inicio = time.perf_counter()

    def gravar(resumo: dict) -> None:
        (SAIDA / "resumo.json").write_text(
            json.dumps(resumo, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def rodar_e9(resumo: dict, geradores: dict) -> None:
        demandas = E.amostra_demandas(args.amostra, args.ids)
        e9 = _e9_existente(resumo)
        for provedor in args.llm:
            modelo = E.modelo_configurado(provedor)
            chave = _chave_execucao(provedor, modelo)
            print(f"E9 — {provedor} ({modelo}): {', '.join(args.condicoes)}; "
                  f"{len(demandas)} demandas")
            resultado = E.e9_llm(provedor, condicoes=args.condicoes, demandas=demandas,
                                 checkpoint=SAIDA / "e9" / f"{chave}.jsonl", refazer=args.refazer)
            linhas = resultado.pop("linhas")
            _csv(f"e9_{chave}.csv", [{k: v for k, v in ln.items() if k not in ("texto", "fontes")}
                                     for ln in linhas])
            resultado["executado_em"] = datetime.now().isoformat(timespec="seconds")
            e9["execucoes"][chave] = resultado
            resumo["e9"] = e9
            gravar(resumo)
            situacao = "concluído" if resultado["concluido"] else \
                f"INCOMPLETO ({resultado['pendentes']} gerações pendentes)"
            print(f"  E9 {chave}: {situacao}")
            # Injecao de prompt (v0.4.1): 6 demandas adversariais x (completo, isolada).
            # Pulada com amostra (cota do Gemini) ou com --sem-injecao.
            if args.sem_injecao or args.amostra or args.ids:
                continue
            print(f"E9 — injeção de prompt — {provedor} ({modelo})")
            inj = E.e9_injecao(provedor, checkpoint=SAIDA / "e9" / f"{chave}_injecao.jsonl",
                               refazer=args.refazer)
            linhas_inj = inj.pop("linhas")
            _csv(f"e9_injecao_{chave}.csv",
                 [{k: v for k, v in ln.items() if k not in ("texto", "fontes")} for ln in linhas_inj])
            inj["executado_em"] = datetime.now().isoformat(timespec="seconds")
            resumo.setdefault("e9_injecao", {"execucoes": {}})["execucoes"][chave] = inj
            gravar(resumo)
            for c in inj["resumo"]:
                print(f"  injeção {c['configuracao']}: {c['injecoes_sucesso']} de {c['geradas']} "
                      f"geradas obedeceram à instrução injetada")

    usar_cache = args.cache_embeddings or args.somente_e9

    def geradores_e9() -> dict:
        geradores = E.geradores_disponiveis()
        if usar_cache and "BGE-M3" in geradores:
            from .cache_embeddings import EmbedderComCache

            geradores["BGE-M3"] = EmbedderComCache(geradores["BGE-M3"])
        return geradores

    if args.somente_e9:
        arquivo = SAIDA / "resumo.json"
        resumo = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}
        geradores = geradores_e9()
        print("Preparando a API (banco temporário + corpus oficial)")
        gerador_api = geradores.get("BGE-M3") or geradores.get("spaCy") or geradores["hash"]
        resumo["api_e9"] = E.preparar_api(gerador_api)
        rodar_e9(resumo, geradores)
        print("Gráficos e tabelas")
        R.gerar_tudo(resumo, SAIDA)
        print(f"E9 concluído em {time.perf_counter() - inicio:.1f} s. Resultados em {SAIDA}")
        return 0

    anterior = SAIDA / "resumo.json"
    resumo_anterior = json.loads(anterior.read_text(encoding="utf-8")) if anterior.exists() else {}
    e9_anterior = _e9_existente(resumo_anterior) if resumo_anterior else {"execucoes": {}}
    injecao_anterior = resumo_anterior.get("e9_injecao")

    resumo: dict = {
        "executado_em": datetime.now().isoformat(timespec="seconds"),
        "ambiente": {
            "python": platform.python_version(),
            "sistema": f"{platform.system()} {platform.release()}",
            "processador": platform.processor() or platform.machine(),
            "nucleos": os.cpu_count(),
            "pacotes": _versoes_pacotes(),
        },
        "corpus_sha256": hashes(),
    }

    print("E5 — minimização de dados pessoais")
    resumo["e5"] = E.e5_minimizacao()

    print("E1 — recuperação por dispositivo")
    geradores = geradores_e9() if args.cache_embeddings else E.geradores_disponiveis()
    resumo["geradores"] = {n: g.identificador for n, g in geradores.items()}
    unidades = unidades_por_dispositivo()
    e1 = E.e1_recuperacao(geradores, unidades)
    recuperadores = e1.pop("_recuperadores")
    _csv("e1_recuperacao_por_demanda.csv", e1.pop("linhas"))
    resumo["e1"] = e1

    print("E3 — portão de suficiência")
    e3 = E.e3_portao(recuperadores)
    resumo["e3"] = e3

    print("E4 — verificação por injeção de falhas")
    bm25 = next(r for r in recuperadores if r.nome == "BM25")
    e4 = E.e4_verificacao(bm25, unidades)
    _csv("e4_injecao_falhas.csv", e4.pop("linhas"))
    resumo["e4"] = e4

    print("E2 — segmentação por dispositivo x blocos")
    resumo["e2"] = E.e2_segmentacao(geradores)

    print("Preparando a API (banco temporário + corpus oficial)")
    gerador_api = geradores.get("BGE-M3") or geradores.get("spaCy") or geradores["hash"]
    resumo["api"] = E.preparar_api(gerador_api)

    print("E7 — cenários funcionais")
    resumo["e7"] = E.e7_cenarios()

    if not args.rapido:
        print("E6 — tempo de resposta por etapa")
        e6 = E.e6_desempenho()
        _csv("e6_desempenho_por_demanda.csv", e6.pop("linhas"))
        resumo["e6"] = e6
        print("E8 — testes automatizados e cobertura")
        resumo["e8"] = E.e8_testes(RAIZ_BACKEND)

    # O E9 de execucoes anteriores (outros provedores) e preservado.
    if e9_anterior["execucoes"]:
        resumo["e9"] = e9_anterior
    if injecao_anterior:
        resumo["e9_injecao"] = injecao_anterior
    # Grava E1 a E8 antes do E9: uma falha do provedor nao apaga o que ja foi medido.
    resumo["duracao_s"] = round(time.perf_counter() - inicio, 1)
    gravar(resumo)

    if args.llm:
        rodar_e9(resumo, geradores)

    resumo["duracao_s"] = round(time.perf_counter() - inicio, 1)
    gravar(resumo)

    print("Gráficos e tabelas")
    R.gerar_tudo(resumo, SAIDA)
    print(f"Concluído em {resumo['duracao_s']} s. Resultados em {SAIDA}")
    print(f"(banco temporário em {pasta})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
