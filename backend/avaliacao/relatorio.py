"""Graficos (PDF e PNG) e tabelas LaTeX a partir de resultados/resumo.json.

Convencoes: numeros no padrao brasileiro (virgula decimal), fonte serifada
compativel com o texto (TeX Gyre Termes, equivalente a Times), marcas finas,
grade recessiva, legenda sempre que houver mais de uma serie e nenhuma cor como
unico canal de identidade (os valores de todos os graficos estao nas tabelas).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import FuncFormatter, PercentFormatter  # noqa: E402

# Paleta categorica de referencia (ordem fixa) e cinza para a linha de base.
AZUL, LARANJA, VERDE_AGUA, AMARELO = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
CINZA, CINZA_GRADE, TEXTO, TEXTO_2 = "#8a8986", "#e4e3df", "#0b0b0b", "#52514e"
# hash em cinza: linha de base sem semantica; o hibrido com o BGE-M3 fica com o amarelo.
CORES_RECUPERADOR = {"hash": CINZA, "spaCy": LARANJA, "BM25": AZUL, "BGE-M3": VERDE_AGUA}


def _fonte() -> None:
    for caminho in font_manager.findSystemFonts():
        if "texgyretermes" in caminho.lower():
            font_manager.fontManager.addfont(caminho)
    nomes = {f.name for f in font_manager.fontManager.ttflist}
    familia = "TeX Gyre Termes" if "TeX Gyre Termes" in nomes else "DejaVu Serif"
    plt.rcParams.update({
        "font.family": familia, "font.size": 10, "axes.titlesize": 10.5,
        "axes.edgecolor": CINZA_GRADE, "axes.linewidth": 0.8, "axes.labelcolor": TEXTO_2,
        "xtick.color": TEXTO_2, "ytick.color": TEXTO_2, "axes.grid": True,
        "grid.color": CINZA_GRADE, "grid.linewidth": 0.8, "grid.linestyle": "-",
        "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
        "savefig.bbox": "tight", "savefig.dpi": 200, "pdf.fonttype": 42,
    })


def _br(valor: float, casas: int = 2) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _pct(valor: float | None, casas: int = 1) -> str:
    if valor is None:
        return "--"
    return f"{valor * 100:.{casas}f}".replace(".", ",") + "\\%"


def _salvar(fig, saida: Path, nome: str) -> None:  # noqa: ANN001
    fig.savefig(saida / "figuras" / f"{nome}.pdf")
    fig.savefig(saida / "figuras" / f"{nome}.png")
    plt.close(fig)


def _cor(nome: str) -> str:
    if nome.startswith("Híbrido"):
        return VERDE_AGUA if "BGE" not in nome else AMARELO
    return CORES_RECUPERADOR.get(nome, CINZA)


# =================================================================== figuras
def fig_revocacao(r: dict, saida: Path) -> None:
    e1 = r["e1"]
    fig, ax = plt.subplots(figsize=(6.3, 3.4))
    ks = [int(k) for k in next(iter(e1["curvas_revocacao"].values()))]
    for nome, curva in e1["curvas_revocacao"].items():
        ys = [curva[str(k)] for k in ks]
        ax.plot(ks, ys, color=_cor(nome), lw=2, marker="o", ms=5,
                markeredgecolor="white", markeredgewidth=1.2, label=nome, solid_capstyle="round")
        ax.annotate(f"{nome}  {_br(ys[-1] * 100, 1)}%", (ks[-1], ys[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8.5, color=TEXTO)
    ax.set_xticks(ks)
    ax.set_xlabel("k (trechos entregues ao modelo)")
    ax.set_ylabel("Revocação@k")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_ylim(0, max(0.6, max(max(c.values()) for c in e1["curvas_revocacao"].values()) + 0.05))
    ax.set_xlim(0.5, ks[-1] + 4.5)
    ax.axvline(6, color=CINZA, lw=1)
    ax.text(6.15, ax.get_ylim()[1] * 0.97, "k do sistema", fontsize=8, color=TEXTO_2, va="top")
    # rotulos diretos no fim de cada curva fazem o papel da legenda
    _salvar(fig, saida, "fig_e1_revocacao")


def fig_segmentacao(r: dict, saida: Path) -> None:
    linhas = [x for x in r["e2"]["resultados"] if x["recuperador"] == "BM25"]
    rotulos = [x["segmentacao"].replace("bloco", "blocos de") .replace("dispositivo", "dispositivo")
               for x in linhas]
    rotulos = [f"{t}\n({x['unidades']} unid.)" if "bloco" in t else f"dispositivo\n({x['unidades']} unid.)"
               for t, x in zip(rotulos, linhas, strict=True)]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(6.3, 3.1), gridspec_kw={"width_ratios": [1.6, 1]})
    largura = 0.34
    pos = range(len(linhas))
    for j, (orc, cor) in enumerate(((600, AZUL), (1800, LARANJA))):
        vals = [x[f"R@{orc}_palavras"] for x in linhas]
        xs = [p + (j - 0.5) * largura for p in pos]
        a1.bar(xs, vals, width=largura * 0.92, color=cor, label=f"{orc} palavras", zorder=3)
        for x, v in zip(xs, vals, strict=True):
            a1.text(x, v + 0.01, _br(v * 100, 0) + "%", ha="center", fontsize=7.5, color=TEXTO)
    a1.set_xticks(list(pos), rotulos, fontsize=8)
    a1.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    a1.set_ylim(0, 0.75)
    a1.set_title("(a) Revocação com o mesmo volume de contexto", loc="left")
    a1.legend(fontsize=8, loc="upper left")
    a1.grid(axis="x", visible=False)
    cit = [x["unidades_citaveis"] for x in linhas]
    a2.barh(list(pos), cit, height=0.45, color=AZUL, zorder=3)
    for p, v in zip(pos, cit, strict=True):
        a2.text(v + 0.02, p, _br(v * 100, 0) + "%", va="center", fontsize=8, color=TEXTO)
    a2.set_yticks(list(pos), [t.split("\n")[0] for t in rotulos], fontsize=8)
    a2.invert_yaxis()
    a2.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    a2.set_xlim(0, 1.22)
    a2.set_title("(b) Unidades citáveis", loc="left")
    a2.grid(axis="y", visible=False)
    fig.tight_layout()
    _salvar(fig, saida, "fig_e2_segmentacao")


def fig_portao(r: dict, saida: Path) -> None:
    pontos = r["e3"]["pontos"]
    res = {x["gerador"]: x for x in r["e3"]["resultados"]}
    nomes = list(pontos)
    fig, eixos = plt.subplots(1, len(nomes), figsize=(6.3, 2.9), sharey=False)
    eixos = eixos if len(nomes) > 1 else [eixos]
    for ax, nome in zip(eixos, nomes, strict=True):
        for j, (chave, cor, rot) in enumerate((("dentro", AZUL, "demandas"), ("fora", LARANJA, "fora do domínio"))):
            vals = pontos[nome][chave]
            xs = [j + ((i % 7) - 3) * 0.045 for i in range(len(vals))]
            ax.scatter(xs, vals, s=22, color=cor, edgecolor="white", linewidth=0.8, zorder=3, label=rot)
        lim = res[nome]["limiar_configurado"]
        if lim is None:
            lim = sum(res[nome]["limiares_cv"]) / 2
        ax.axhline(lim, color=TEXTO_2, lw=1)
        ax.text(1.45, lim, f"limiar {_br(lim, 2)}", fontsize=7.5, color=TEXTO_2, va="bottom", ha="right")
        anterior = res[nome].get("limiar_anterior")
        if anterior is not None:
            ax.axhline(anterior, color=CINZA, lw=1)
            ax.text(1.45, anterior, f"limiar da v0.2.0: {_br(anterior, 2)}", fontsize=7.5,
                    color=CINZA, va="bottom", ha="right")
        ax.set_xticks([0, 1], ["dentro", "fora"])
        ax.set_xlim(-0.5, 1.5)
        titulo = "BM25 (não usado no portão)" if nome == "BM25" else nome
        ax.set_title(f"{titulo}\nAUC {_br(res[nome]['auc'], 3)}", loc="left", fontsize=9)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _br(v, 1)))
        ax.grid(axis="x", visible=False)
    eixos[0].set_ylabel("Maior similaridade com a base")
    fig.legend(*eixos[0].get_legend_handles_labels(), fontsize=8, ncol=2, loc="upper center",
               bbox_to_anchor=(0.5, 1.06))
    fig.tight_layout()
    _salvar(fig, saida, "fig_e3_portao")


def fig_verificacao(r: dict, saida: Path) -> None:
    resumo = [x for x in r["e4"]["resumo"] if x["tipo"] != "C0"]
    fig, ax = plt.subplots(figsize=(6.3, 3.3))
    pos = list(range(len(resumo)))
    altura = 0.36
    for j, (versao, cor, rot) in enumerate((("v0.2.0", CINZA, "versão 0.2.0"), ("v0.3", AZUL, "versão 0.3"))):
        vals = [x[versao] for x in resumo]
        ys = [p + (j - 0.5) * altura for p in pos]
        ax.barh(ys, vals, height=altura * 0.9, color=cor, label=rot, zorder=3)
    ax.set_yticks(pos, [f"{x['tipo']} {x['descricao']}" for x in resumo], fontsize=8.5)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlim(0, 1.02)
    ax.set_xlabel("Falhas detectadas")
    ax.legend(fontsize=8, ncol=2, loc="lower left", bbox_to_anchor=(0, 1.0))
    ax.grid(axis="y", visible=False)
    _salvar(fig, saida, "fig_e4_verificacao")


def fig_minimizacao(r: dict, saida: Path) -> None:
    teste = r["e5"]["conjuntos"]["teste"]
    versoes = [("v0.2.0", CINZA), ("v0.3 (regex)", LARANJA), ("v0.3 (regex + NER)", AZUL)]
    tipos = list(teste["v0.2.0"]["revocacao_por_tipo"])
    itens = teste["v0.2.0"]["itens_por_tipo"]
    fig, ax = plt.subplots(figsize=(6.3, 3.2))
    largura = 0.26
    for j, (versao, cor) in enumerate(versoes):
        vals = [teste[versao]["revocacao_por_tipo"][t] for t in tipos]
        xs = [i + (j - 1) * largura for i in range(len(tipos))]
        ax.bar(xs, vals, width=largura * 0.9, color=cor, label=versao, zorder=3)
    ax.set_xticks(range(len(tipos)), [f"{t}\n(n={itens[t]})" for t in tipos], fontsize=8.5)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_ylim(0, 1.18)
    ax.set_ylabel("Dados pessoais removidos")
    ax.legend(fontsize=8, ncol=3, loc="upper left")
    ax.grid(axis="x", visible=False)
    _salvar(fig, saida, "fig_e5_minimizacao")


def fig_latencia(r: dict, saida: Path) -> None:
    nomes = {"registrar_ms": "Registrar demanda", "etapa_recuperacao_ms": "Recuperação",
             "etapa_geracao_ms": "Geração (provedor determinístico)",
             "etapa_verificacao_ms": "Normalização, verificação e análise",
             "etapa_persistencia_ms": "Persistência e auditoria", "gerar_ms": "Gerar minuta (HTTP)",
             "validar_ms": "Validar", "exportar_ms": "Exportar .docx"}
    dados = {x["medida"]: x for x in r["e6"]["resumo"]}
    ordem = [k for k in nomes if k in dados]
    fig, ax = plt.subplots(figsize=(6.3, 3.1))
    med = [dados[k]["mediana_ms"] for k in ordem]
    p95 = [dados[k]["p95_ms"] for k in ordem]
    pos = list(range(len(ordem)))
    ax.barh(pos, med, height=0.45, color=AZUL, zorder=3, label="mediana")
    ax.scatter(p95, pos, marker="|", s=160, color=TEXTO, zorder=4, label="percentil 95")
    for p, m, q in zip(pos, med, p95, strict=True):
        ax.text(q + 3, p, f"{_br(m, 1)} ms", va="center", fontsize=7.5, color=TEXTO)
    ax.set_yticks(pos, [nomes[k] for k in ordem], fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlabel("Tempo (ms)")
    ax.set_xlim(0, max(p95) * 1.25)
    ax.legend(fontsize=8, ncol=2, loc="lower left", bbox_to_anchor=(0, 1.0))
    ax.grid(axis="y", visible=False)
    _salvar(fig, saida, "fig_e6_latencia")


NOMES_PROVEDOR = {"anthropic": "Anthropic", "gemini": "Gemini", "groq": "Groq", "ollama": "Ollama"}
ORDEM_CONDICOES = ("isolada", "sem_rag", "completo_bm25", "completo")
CORES_CONDICAO = {"isolada": CINZA, "sem_rag": LARANJA, "completo_bm25": AMARELO, "completo": AZUL}


def execucoes_e9(r: dict) -> list[tuple[str, dict]]:
    """Execucoes do E9 (uma por provedor/modelo), aceitando o formato antigo."""
    e9 = r.get("e9") or {}
    if "execucoes" in e9:
        return [(k, v) for k, v in e9["execucoes"].items() if v.get("resumo")]
    return [("e9", e9)] if e9.get("resumo") else []


def _titulo_execucao(x: dict) -> str:
    modelo = (x.get("modelos") or [x.get("modelo_configurado", "")])[0]
    return f"{NOMES_PROVEDOR.get(x.get('provedor', ''), x.get('provedor', ''))} ({modelo})"


def fig_llm(r: dict, saida: Path) -> None:
    execucoes = execucoes_e9(r)
    if not execucoes:
        return
    metricas = [("precisao_macro", "Precisão das\ncitações"),
                ("citacoes_existentes", "Citações a artigos\nexistentes"),
                ("valores_presentes", "Valores da demanda\nno texto"),
                ("minutas_com_norma_revogada", "Citam norma revogada\n(menor é melhor)")]
    fig, eixos = plt.subplots(len(execucoes), 1, figsize=(6.3, 2.9 * len(execucoes)), squeeze=False)
    for (_chave, x), ax in zip(execucoes, eixos[:, 0], strict=True):
        dados = {c["condicao"]: c for c in x["resumo"] if "condicao" in c}
        ordem = [c for c in ORDEM_CONDICOES if c in dados]
        largura = 0.8 / max(len(ordem), 1)
        for j, cond in enumerate(ordem):
            for i, (chave, _rot) in enumerate(metricas):
                pos = i + (j - (len(ordem) - 1) / 2) * largura
                valor = dados[cond].get(chave)
                if valor is None:
                    ax.text(pos, 0.02, "n/a", ha="center", va="bottom", fontsize=6.5, color=TEXTO_2)
                    continue
                ax.bar(pos, valor, width=largura * 0.9, color=CORES_CONDICAO[cond], zorder=3)
                ax.text(pos, valor + 0.015, f"{valor * 100:.0f}", ha="center", va="bottom",
                        fontsize=6.5, color=TEXTO)
        ax.set_xticks(range(len(metricas)), [m[1] for m in metricas], fontsize=8.5)
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
        ax.set_ylim(0, 1.22)
        ax.set_title(_titulo_execucao(x), loc="left", fontsize=9)
        ax.legend(handles=[Patch(color=CORES_CONDICAO[c], label=dados[c]["configuracao"]) for c in ordem],
                  fontsize=7.5, ncol=min(len(ordem), 4), loc="upper left")
        ax.grid(axis="x", visible=False)
    fig.tight_layout()
    _salvar(fig, saida, "fig_e9_llm")
    plt.close(fig)


# =================================================================== tabelas
def _tabela(saida: Path, nome: str, legenda: str, rotulo: str, colunas: str,
            cabecalho: list[str], linhas: list[list[str]], nota: str = "",
            tamanho: str = "small") -> None:
    corpo = "\n".join(" & ".join(c) + r" \\" for c in linhas)
    fonte = r"{\small \textbf{Fonte:} Elaborado pelo autor (2026), a partir de \texttt{avaliacao/resultados/resumo.json}.}"
    if nota:
        fonte = fonte + r"\par" + "\n" + r"{\small \textbf{Nota:} " + nota + r"}"
    texto = (
        "% Gerado por avaliacao/relatorio.py — nao editar a mao; rode novamente a avaliacao.\n"
        "\\begin{table}[htbp]\n\\caption{" + legenda + "}\\label{" + rotulo + "}\n"
        "\\centering\\" + tamanho + "\n\\setlength{\\tabcolsep}{4pt}\n\\begin{tabular}{" + colunas + "}\n\\toprule\n"
        + " & ".join(cabecalho) + r" \\" + "\n\\midrule\n" + corpo + "\n\\bottomrule\n\\end{tabular}\n"
        "\\par\\smallskip\n\\begin{minipage}{\\linewidth}" + fonte + "\\end{minipage}\n\\end{table}\n"
    )
    (saida / "latex" / f"{nome}.tex").write_text(texto, encoding="utf-8")


def tabelas(r: dict, saida: Path) -> None:
    # ---------------------------------------------------------------- E8
    if "e8" in r:
        e8 = r["e8"]
        from .experimentos import GRUPOS_TESTE

        def esc(t: str) -> str:
            return t.replace("_", "\\_")

        linhas = [[esc(GRUPOS_TESTE.get(x["arquivo"], x["grupo"])), f"\\texttt{{{esc(x['arquivo'])}}}",
                   str(x["testes"]), str(x["falhas"])] for x in e8["por_arquivo"]]
        linhas.append([r"\textbf{Total}", "", f"\\textbf{{{e8['total']}}}", f"\\textbf{{{e8['falhas']}}}"])
        _tabela(saida, "tab_e8_testes", "Testes automatizados por grupo", "tab:testes",
                "p{6.2cm}lrr", ["Grupo", "Arquivo", "Testes", "Falhas"], linhas,
                f"cobertura de instruções do pacote \\texttt{{app}}: {_br(e8['cobertura_total'], 1)}\\%; "
                f"tempo de execução: {_br(e8['duracao_s'], 1)} s.")
    # ---------------------------------------------------------------- E1
    linhas = []
    for a in r["e1"]["agregado"]:
        ic = a["R@6_ic95"]
        linhas.append([a["recuperador"], _pct(a["P@1"]), _pct(a["R@3"]),
                       f"{_pct(a['R@6'])} ({_br(ic[0] * 100, 0)}--{_br(ic[1] * 100, 0)})",
                       _pct(a["R@10"]), _pct(a["Hit@6"]), _br(a["MRR"], 3), _pct(a["R@6_artigo"])])
    _tabela(saida, "tab_e1_recuperacao", "Desempenho da recuperação por dispositivo", "tab:recuperacao",
            "lrrrrrrr", ["Recuperador", "P@1", "R@3", "R@6 (IC 95\\%)", "R@10", "Acerto@6", "MRR",
                         "R@6 artigo"], linhas,
            f"{r['e1']['demandas']} demandas, {r['e1']['relevantes']} dispositivos relevantes no gabarito, "
            f"{_milhar(r['e1']['unidades'])} dispositivos na base. IC: intervalo de confiança por bootstrap "
            "(2.000 reamostragens). R@6 artigo: acerto quando o dispositivo recuperado pertence ao mesmo "
            "artigo do esperado.", tamanho="footnotesize")
    # ---------------------------------------------------------------- E2
    linhas = []
    for x in r["e2"]["resultados"]:
        linhas.append([x["segmentacao"].replace("bloco", "blocos de"), x["recuperador"], _milhar(x["unidades"]),
                       _pct(x["R@6"]), _pct(x["R@600_palavras"]), _pct(x["R@1800_palavras"]),
                       _milhar(int(x["palavras_contexto_mediana"])), _pct(x["unidades_citaveis"], 0)])
    _tabela(saida, "tab_e2_segmentacao", "Segmentação por dispositivo e por blocos de tamanho fixo",
            "tab:segmentacao", "llrrrrrr",
            ["Segmentação", "Recup.", "Unid.", "R@6", "R@600p", "R@1800p", "Palavras@6", "Citáveis"],
            linhas,
            "blocos com 20\\% de sobreposição; um bloco cobre um dispositivo quando contém ao menos metade "
            "de suas palavras. R@600p e R@1800p: revocação com o mesmo volume de contexto (palavras). "
            "Citáveis: unidades recuperadas que correspondem a um único dispositivo.", tamanho="footnotesize")
    # ---------------------------------------------------------------- E3
    linhas = []
    for x in r["e3"]["resultados"]:
        cfg = x["limiar_configurado"]
        ant = x.get("limiar_anterior")
        linhas.append([x["gerador"], _br(x["auc"], 3), _br(x["spearman_tamanho"], 2),
                       f"{_br(ant, 2)}: {_pct(x['tpr_anterior'], 0)} / {_pct(x['fpr_anterior'], 0)}" if ant is not None else "--",
                       " e ".join(_br(v, 2) for v in x["limiares_cv"]),
                       f"{_pct(x['tpr_cv'], 0)} / {_pct(x['fpr_cv'], 0)}",
                       f"{_br(cfg, 2)}: {_pct(x['tpr_configurado'], 0)} / {_pct(x['fpr_configurado'], 0)}" if cfg is not None else "não se aplica"])
    _tabela(saida, "tab_e3_portao", "Portão de suficiência: separação entre demandas e pedidos fora do domínio",
            "tab:portao", "lrrlrrl",
            ["Pontuação", "AUC", "$\\rho$", "v0.2.0: TPR / FPR", "Limiares (VC)", "TPR / FPR (VC)",
             "Adotado: TPR / FPR"],
            linhas,
            f"{r['e3']['dentro']} demandas e {r['e3']['fora']} pedidos fora do domínio. TPR: demandas aceitas; "
            "FPR: pedidos fora do domínio aceitos. VC: validação cruzada em duas partes (limiar escolhido "
            "numa metade pelo índice de Youden e aplicado à outra). $\\rho$: correlação de Spearman entre "
            "o tamanho da consulta e a pontuação, entre as demandas.", tamanho="footnotesize")
    # ---------------------------------------------------------------- E4
    linhas = [[x["tipo"], x["descricao"], str(x["casos"]), _pct(x["v0.2.0"], 0), _pct(x["v0.3"], 0)]
              for x in r["e4"]["resumo"]]
    _tabela(saida, "tab_e4_verificacao", "Detecção de falhas injetadas pela verificação automática",
            "tab:verificacao", "llrrr", ["Tipo", "Falha injetada", "Casos", "v0.2.0", "v0.3"], linhas,
            "no controle (C0), o valor é a taxa de falso positivo; nos demais, a taxa de detecção.")
    # ---------------------------------------------------------------- E5
    for conj, legenda in (("teste", "conjunto de teste retido"), ("desenvolvimento", "conjunto de desenvolvimento")):
        c = r["e5"]["conjuntos"][conj]
        tipos = list(c["v0.2.0"]["revocacao_por_tipo"])
        linhas = [[t, str(c["v0.2.0"]["itens_por_tipo"][t])] + [
            _pct(c[v]["revocacao_por_tipo"][t], 0) for v in ("v0.2.0", "v0.3 (regex)", "v0.3 (regex + NER)")]
            for t in tipos]
        linhas.append([r"\textbf{Global}", str(c["v0.2.0"]["itens"])] + [
            f"\\textbf{{{_pct(c[v]['revocacao_global'], 0)}}}" for v in ("v0.2.0", "v0.3 (regex)", "v0.3 (regex + NER)")])
        linhas.append(["Falsos positivos", f"{c['_tamanho']['negativos']} textos"] + [
            str(c[v]["falsos_positivos"]) for v in ("v0.2.0", "v0.3 (regex)", "v0.3 (regex + NER)")])
        _tabela(saida, f"tab_e5_minimizacao_{conj}",
                f"Minimização de dados pessoais --- {legenda}", f"tab:minimizacao_{conj[:5]}",
                "lrrrr", ["Tipo", "Itens", "v0.2.0", "v0.3 (regex)", "v0.3 (regex + NER)"], linhas,
                "proporção de dados pessoais removidos do texto; falsos positivos: textos sem dado pessoal "
                "alterados pela minimização.")
    # ---------------------------------------------------------------- E6
    if "e6" in r:
        nomes = {"registrar_ms": "Registrar demanda (HTTP)", "etapa_recuperacao_ms": "Recuperação",
                 "etapa_geracao_ms": "Geração (provedor determinístico)",
                 "etapa_verificacao_ms": "Normalização, verificação e análise",
                 "etapa_persistencia_ms": "Persistência e auditoria", "etapa_total_ms": "Total do orquestrador",
                 "gerar_ms": "Gerar minuta (HTTP)", "validar_ms": "Validar (HTTP)", "exportar_ms": "Exportar .docx (HTTP)"}
        dados = {x["medida"]: x for x in r["e6"]["resumo"]}
        linhas = [[nomes[k], str(dados[k]["n"]), _br(dados[k]["mediana_ms"], 1), _br(dados[k]["p95_ms"], 1),
                   _br(dados[k]["max_ms"], 1)] for k in nomes if k in dados]
        _tabela(saida, "tab_e6_latencia", "Tempo de resposta por etapa (ms)", "tab:latencia", "lrrrr",
                ["Etapa", "n", "Mediana", "P95", "Máximo"], linhas,
                "provedor de linguagem determinístico; o tempo do modelo de linguagem real não está incluído.")
    # ---------------------------------------------------------------- E9
    execucoes = execucoes_e9(r)
    if execucoes:
        linhas, falhas = [], 0
        for _chave, x in execucoes:
            linhas.append([f"\\multicolumn{{10}}{{l}}{{\\textit{{{_titulo_execucao(x)}}}}}"])
            dados = {c["condicao"]: c for c in x["resumo"]}
            for cond in ORDEM_CONDICOES:
                if cond not in dados:
                    continue
                c = dados[cond]
                falhas += c.get("falhas_provedor", 0)
                linhas.append([
                    "\\quad " + c["configuracao"], str(c["n"]), _br(c["citacoes_por_minuta"], 1),
                    _pct(c.get("precisao_macro")), str(c["citacoes_inexistentes"]),
                    _pct(c.get("minutas_com_norma_revogada"), 0), _pct(c["valores_presentes"]),
                    _pct(c["ancoragem_media"]), _pct(c["com_inconsistencia"], 0),
                    _br(c["latencia_mediana_s"], 1)])
        corpo_nota = (
            "Cit./min.: citações a artigo por minuta; Precisão: média, por minuta, da fração das "
            "citações que coincidem com um dispositivo do gabarito; Inexist.: citações a artigo que não "
            "existe na norma; Revog.: minutas que citam norma revogada pela Lei nº 14.133/2021 "
            "(Lei nº 8.666/1993, Lei nº 10.520/2002 ou Lei nº 12.462/2011); Valores: fração dos valores da "
            "demanda (quantidade, valor, data, prioridade) presentes no texto. Ancoragem e inconsistência "
            "vêm da verificação do sistema e não se aplicam à LLM isolada (--). n exclui as demandas "
            f"barradas pelo portão; falhas do provedor não refeitas: {falhas}.")
        _tabela(saida, "tab_e9_llm", "Sistema completo, sistema sem RAG e LLM isolada", "tab:llm",
                "lrrrrrrrrr", ["Condição", "n", "Cit./min.", "Precisão", "Inexist.", "Revog.", "Valores",
                              "Ancoragem", "Inconsist.", "Lat. (s)"],
                linhas, corpo_nota, tamanho="footnotesize")
        # comparacoes pareadas
        nomes_cond = {"completo": "completo", "completo_bm25": "RAG BM25", "sem_rag": "sem RAG",
                      "isolada": "LLM isolada"}
        nomes_met = {"citacoes_pertinentes": "Citações pertinentes (nº)",
                     "precisao_citacoes": "Precisão das citações",
                     "taxa_inexistentes": "Citações a artigo inexistente",
                     "valores_presentes": "Valores da demanda no texto"}
        linhas = []
        for _chave, x in execucoes:
            comp = x.get("comparacoes") or []
            if not comp:
                continue
            linhas.append([f"\\multicolumn{{4}}{{l}}{{\\textit{{{_titulo_execucao(x)}}}}}"])
            for c in comp:
                if c["b"] not in ("isolada", "sem_rag", "completo_bm25") or c["a"] != "completo":
                    continue
                contagem = c["metrica"] == "citacoes_pertinentes"
                fmt = (lambda v: _br(v, 2)) if contagem else (lambda v: _br(v * 100, 1) + " p.p.")
                linhas.append([f"\\quad completo $-$ {nomes_cond[c['b']]}", nomes_met[c["metrica"]],
                               str(c["n"]),
                               f"{fmt(c['diferenca'])} ({fmt(c['ic95'][0])} a {fmt(c['ic95'][1])})"])
        if linhas:
            _tabela(saida, "tab_e9_pareado", "Diferenças pareadas entre as condições do E9",
                    "tab:llm-pareado", "llrl", ["Comparação", "Métrica", "n", "Diferença (IC 95\\%)"],
                    linhas, "diferença média por demanda entre as condições, com intervalo de confiança "
                    "por bootstrap (2.000 reamostragens); intervalo que não contém zero indica diferença "
                    "sustentada pelos dados. Precisão e citações inexistentes só são definidas para "
                    "minutas com ao menos uma citação.", tamanho="footnotesize")


def gerar_tudo(resumo: dict[str, Any], saida: Path) -> None:
    _fonte()
    (saida / "figuras").mkdir(parents=True, exist_ok=True)
    (saida / "latex").mkdir(parents=True, exist_ok=True)
    fig_revocacao(resumo, saida)
    fig_segmentacao(resumo, saida)
    fig_portao(resumo, saida)
    fig_verificacao(resumo, saida)
    fig_minimizacao(resumo, saida)
    if "e6" in resumo:
        fig_latencia(resumo, saida)
    if "e9" in resumo:
        fig_llm(resumo, saida)
    tabelas(resumo, saida)


if __name__ == "__main__":
    pasta = Path(__file__).resolve().parent / "resultados"
    gerar_tudo(json.loads((pasta / "resumo.json").read_text(encoding="utf-8")), pasta)
    print("figuras e tabelas regeneradas em", pasta)
