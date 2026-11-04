"""Experimentos do capitulo de Analise de Resultados.

Cada funcao devolve um dicionario serializavel com os numeros agregados e, quando
cabe, as linhas por caso (para os CSVs e para conferencia). Nenhuma funcao
escreve arquivos: isso fica com executar.py.

  E1  recuperacao por dispositivo (P@k, R@k, MRR, nDCG) — hash, spaCy, BM25 e,
      quando instalado, BGE-M3
  E2  segmentacao por dispositivo x blocos de tamanho fixo
  E3  portao de suficiencia (fluxo A1): AUC e limiar por validacao cruzada
  E4  verificacao automatica por injecao de falhas (v0.2.0 x v0.3)
  E5  minimizacao de dados pessoais (v0.2.0 x v0.3, com e sem NER)
  E6  tempo de resposta por etapa, pela API
  E7  cenarios funcionais da secao 3.4.2, pela API
  E8  testes automatizados (pytest) e cobertura
  E9  geracao com LLM real: sistema completo x sem RAG x LLM isolada (requer provedor)
"""
from __future__ import annotations

import copy
import importlib
import json
import random
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

from . import metricas as M
from .corpus import RAIZ_AVALIACAO, Unidade, artigo_de, dispositivos, gabarito
from .recuperadores import Recuperador, RecuperadorBM25, RecuperadorDenso, RecuperadorHibrido

KS = (1, 3, 5, 6, 10)
ORCAMENTOS = (600, 1800)     # palavras de contexto entregues ao modelo (E2)
K_SISTEMA = 6


# ===================================================================== apoio
def geradores_disponiveis() -> dict[str, Any]:
    """Geradores de embeddings que podem ser executados neste ambiente."""
    from app.core.embeddings import EmbedderHash

    saida: dict[str, Any] = {"hash": EmbedderHash()}
    try:
        from app.core.embeddings import EmbedderSpacy

        saida["spaCy"] = EmbedderSpacy()
    except Exception as erro:  # noqa: BLE001
        print(f"[aviso] spaCy indisponível: {erro}")
    try:
        from app.core.embeddings import EmbedderBGE

        saida["BGE-M3"] = EmbedderBGE("BAAI/bge-m3")
    except Exception as erro:  # noqa: BLE001
        print(f"[aviso] BGE-M3 indisponível neste ambiente ({type(erro).__name__}); "
              "o experimento segue sem ele. Instale sentence-transformers para incluí-lo.")
    return saida


def _consulta(descricao: str) -> str:
    """A consulta e a descricao ja minimizada, como no sistema (RN06)."""
    from app.core.anonimizador import minimizar

    return minimizar(descricao).texto


def _avaliar(rec: Recuperador, cobertura: dict[str, set[str]], demandas: list[dict],
             kmax: int = 10) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    linhas: list[dict[str, Any]] = []
    tempos: list[float] = []
    for d in demandas:
        consulta = _consulta(d["descricao"])
        inicio = time.perf_counter()
        resultado = rec.buscar(consulta, kmax)
        tempos.append((time.perf_counter() - inicio) * 1000)
        cob = [cobertura[i] for i, _ in resultado]
        relevantes = set(d["relevantes"])
        arts_rel = {artigo_de(r) for r in relevantes}
        cob_art = [{artigo_de(x) for x in c} for c in cob]
        linha: dict[str, Any] = {"id": d["id"], "recuperador": rec.nome}
        for k in KS:
            linha[f"P@{k}"] = M.precisao_k(cob, relevantes, k)
            linha[f"R@{k}"] = M.revocacao_k(cob, relevantes, k)
            linha[f"Hit@{k}"] = M.acerto_k(cob, relevantes, k)
        linha["MRR"] = M.rr(cob, relevantes)
        linha["nDCG@6"] = M.ndcg_k(cob, relevantes, K_SISTEMA)
        linha["R@6_artigo"] = M.revocacao_k(cob_art, arts_rel, K_SISTEMA)
        linha["P@6_artigo"] = M.precisao_k(cob_art, arts_rel, K_SISTEMA)
        linha["dispositivos_por_unidade"] = M.media(len(c) for c in cob[:K_SISTEMA]) if cob else 0
        linha["unidades_citaveis"] = (
            sum(1 for c in cob[:K_SISTEMA] if len(c) == 1) / len(cob[:K_SISTEMA]) if cob else 0
        )
        linha["top6"] = " ; ".join(i for i, _ in resultado[:K_SISTEMA])
        linhas.append(linha)
    agregado: dict[str, Any] = {"recuperador": rec.nome, "n": len(linhas),
                                "indexacao_s": round(rec.tempo_indexacao_s, 2),
                                "consulta_ms_mediana": round(statistics.median(tempos), 2)}
    for chave in [c for c in linhas[0] if c not in {"id", "recuperador", "top6"}]:
        valores = [ln[chave] for ln in linhas]
        agregado[chave] = round(M.media(valores), 4)
    for chave in ("R@6", "MRR", "R@6_artigo"):
        baixo, alto = M.ic_bootstrap([ln[chave] for ln in linhas])
        agregado[f"{chave}_ic95"] = [round(baixo, 4), round(alto, 4)]
    return agregado, linhas


# ======================================================================= E1
def e1_recuperacao(geradores: dict[str, Any], unidades: list[Unidade]) -> dict[str, Any]:
    g = gabarito()
    cobertura = {u.ident: set(u.cobre) for u in unidades}
    densos = [RecuperadorDenso(nome, emb, unidades) for nome, emb in geradores.items()]
    bm25 = RecuperadorBM25(unidades)
    recuperadores: list[Recuperador] = [*densos, bm25]
    # hibrido com o melhor gerador semantico disponivel (BGE-M3 > spaCy)
    for preferido in ("BGE-M3", "spaCy"):
        base = next((r for r in densos if r.nome == preferido), None)
        if base is not None:
            recuperadores.append(RecuperadorHibrido(base, bm25))
            break
    agregados, linhas = [], []
    for rec in recuperadores:
        a, ls = _avaliar(rec, cobertura, g["demandas"])
        agregados.append(a)
        linhas.extend(ls)
    curvas = {
        a["recuperador"]: {str(k): a[f"R@{k}"] for k in KS} for a in agregados
    }
    return {
        "unidades": len(unidades),
        "demandas": len(g["demandas"]),
        "relevantes": sum(len(d["relevantes"]) for d in g["demandas"]),
        "agregado": agregados,
        "curvas_revocacao": curvas,
        "linhas": linhas,
        "_recuperadores": recuperadores,      # reaproveitados no E3 (nao serializado)
    }


# ======================================================================= E2
def e2_segmentacao(geradores: dict[str, Any], tamanhos: tuple[int, ...] = (100, 300)
                   ) -> dict[str, Any]:
    from .corpus import unidades_por_bloco, unidades_por_dispositivo

    g = gabarito()
    configuracoes = {"dispositivo": unidades_por_dispositivo()}
    for n in tamanhos:
        configuracoes[f"bloco {n}"] = unidades_por_bloco(n, 0.2)
    resultados: list[dict[str, Any]] = []
    for segmentacao, unidades in configuracoes.items():
        cobertura = {u.ident: set(u.cobre) for u in unidades}
        palavras = {u.ident: len(u.texto_indexado.split()) for u in unidades}
        recs: list[Recuperador] = [RecuperadorBM25(unidades)]
        recs += [RecuperadorDenso(nome, emb, unidades) for nome, emb in geradores.items()]
        for rec in recs:
            agregado, linhas = _avaliar(rec, cobertura, g["demandas"])
            contexto = []
            por_orcamento: dict[int, list[float]] = {o: [] for o in ORCAMENTOS}
            for d in g["demandas"]:
                lista = [i for i, _ in rec.buscar(_consulta(d["descricao"]), 80)]
                contexto.append(sum(palavras[i] for i in lista[:K_SISTEMA]))
                relevantes = set(d["relevantes"])
                for orcamento in ORCAMENTOS:
                    usados, soma = [], 0
                    for i in lista:
                        if usados and soma + palavras[i] > orcamento:
                            break
                        usados.append(cobertura[i])
                        soma += palavras[i]
                    por_orcamento[orcamento].append(M.revocacao_k(usados, relevantes, len(usados)))
            resultados.append({
                "segmentacao": segmentacao,
                "recuperador": rec.nome,
                "unidades": len(unidades),
                "R@6": agregado["R@6"],
                "P@6": agregado["P@6"],
                "MRR": agregado["MRR"],
                "R@6_artigo": agregado["R@6_artigo"],
                "dispositivos_por_unidade": agregado["dispositivos_por_unidade"],
                "unidades_citaveis": agregado["unidades_citaveis"],
                "palavras_contexto_mediana": statistics.median(contexto),
                **{f"R@{o}_palavras": round(M.media(v), 4) for o, v in por_orcamento.items()},
            })
    return {"tamanhos": list(tamanhos), "sobreposicao": 0.2, "resultados": resultados}


# ======================================================================= E3
def e3_portao(recuperadores: list[Recuperador]) -> dict[str, Any]:
    """Similaridade do melhor trecho, dentro x fora do dominio (fluxo A1)."""
    from app.config import config

    g = gabarito()
    # limiar da versao 0.2.0 (calibrado sobre as amostras) e limiar adotado na 0.3
    anterior = {"hash": 0.10, "BGE-M3": 0.45}
    configurado = {"hash": config.RECUPERACAO_LIMIAR_HASH,
                   "spaCy": config.RECUPERACAO_LIMIAR_SPACY,
                   "BGE-M3": config.RECUPERACAO_LIMIAR,
                   "BM25": None}
    saida: list[dict[str, Any]] = []
    pontos: dict[str, dict[str, list[float]]] = {}
    for rec in recuperadores:
        if not isinstance(rec, (RecuperadorDenso, RecuperadorBM25)):
            continue

        def melhor(texto: str) -> float:
            achados = rec.buscar(_consulta(texto), 1)
            return achados[0][1] if achados else 0.0

        dentro = [melhor(d["descricao"]) for d in g["demandas"]]
        fora = [melhor(d["descricao"]) for d in g["fora_do_dominio"]]
        pontos[rec.nome] = {"dentro": dentro, "fora": fora}
        # validacao cruzada em 2 partes: o limiar escolhido numa metade e
        # aplicado a outra, para nao medir o limiar sobre os dados que o definiram
        vp = fp = 0
        limiares = []
        for parte in (0, 1):
            treino_d = [v for i, v in enumerate(dentro) if i % 2 != parte]
            treino_f = [v for i, v in enumerate(fora) if i % 2 != parte]
            teste_d = [v for i, v in enumerate(dentro) if i % 2 == parte]
            teste_f = [v for i, v in enumerate(fora) if i % 2 == parte]
            limiar = M.limiar_youden(treino_d, treino_f)
            limiares.append(limiar)
            vp += sum(v >= limiar for v in teste_d)
            fp += sum(v >= limiar for v in teste_f)
        tamanhos = [len(_consulta(d["descricao"]).split()) for d in g["demandas"]]
        lim_cfg = configurado.get(rec.nome)
        saida.append({
            "gerador": rec.nome,
            "auc": round(M.auc(dentro, fora), 4),
            "dentro_min": round(min(dentro), 4), "dentro_mediana": round(statistics.median(dentro), 4),
            "fora_max": round(max(fora), 4), "fora_mediana": round(statistics.median(fora), 4),
            "limiar_anterior": anterior.get(rec.nome),
            "tpr_anterior": round(sum(v >= anterior[rec.nome] for v in dentro) / len(dentro), 4) if rec.nome in anterior else None,
            "fpr_anterior": round(sum(v >= anterior[rec.nome] for v in fora) / len(fora), 4) if rec.nome in anterior else None,
            "limiar_configurado": lim_cfg,
            "tpr_configurado": round(sum(v >= lim_cfg for v in dentro) / len(dentro), 4) if lim_cfg is not None else None,
            "fpr_configurado": round(sum(v >= lim_cfg for v in fora) / len(fora), 4) if lim_cfg is not None else None,
            "limiares_cv": [round(x, 4) for x in limiares],
            "tpr_cv": round(vp / len(dentro), 4),
            "fpr_cv": round(fp / len(fora), 4),
            "limiar_sugerido": round(M.limiar_youden(dentro, fora), 4),
            # correlacao entre o tamanho da consulta e a pontuacao, so dentro do
            # dominio: mede se o portao responderia ao tamanho, e nao ao assunto
            "spearman_tamanho": round(M.spearman(tamanhos, dentro), 4),
        })
    return {"dentro": len(g["demandas"]), "fora": len(g["fora_do_dominio"]),
            "resultados": saida, "pontos": pontos}


# ======================================================================= E4
TIPOS_FALHA = {
    "C0": "Controle (sem falha)",
    "F1": "Fundamento inexistente (T9)",
    "F2": "Artigo fora do contexto",
    "F3": "Artigo de outra norma, mesmo número",
    "F4": "Norma revogada citada (Lei 8.666/1993)",
    "F5": "Artigos no plural fora do contexto",
    "F6": "Campo redigido sem fundamento",
    "F7": "Valor extraído inventado",
    "F8": "Evidência parafraseada",
}


def _minuta_base(descricao: str, contexto: list[dict[str, Any]]) -> dict[str, Any]:
    """Minuta produzida pelo caminho real (prompt -> provedor deterministico -> normalizacao)."""
    from app.core.conteudo import normalizar_conteudo
    from app.llm import prompt as P
    from app.llm.provedores import ProvedorEco

    bloco = "\n\n".join(f"[{c['marcador']}] {c['referencia']} — {c['texto']}" for c in contexto)
    texto_usuario = P.montar_prompt_usuario(
        unidade="Unidade requisitante", descricao=descricao, bloco_contexto=bloco,
        formato=json.dumps(P.MODELO_SAIDA, ensure_ascii=False),
    )
    bruto = ProvedorEco().completar(P.PROMPT_SISTEMA, texto_usuario).como_json()
    return normalizar_conteudo(bruto)


def _injetar(tipo: str, minuta: dict[str, Any], contexto: list[dict[str, Any]],
             rnd: random.Random, demanda: dict[str, Any]) -> dict[str, Any] | None:
    from app.core.verificacao import mencoes

    m = copy.deepcopy(minuta)
    just = m["justificativa_necessidade"]
    pares = {(n or "", a) for c in contexto for a, n in mencoes(f"{c['referencia']} {c['texto']}")}
    numeros = {a for _, a in pares}
    norma_ctx = {"Lei nº 14.133/2021": "14133", "Decreto nº 10.947/2022": "10947",
                 "Lei nº 13.709/2018": "13709"}

    def artigo_livre(maximo: int) -> int:
        livres = [n for n in range(1, maximo + 1) if str(n) not in numeros]
        return rnd.choice(livres)

    if tipo == "C0":
        return m
    if tipo == "F1":
        just["fundamentos"] = [*just["fundamentos"], "T9"]
    elif tipo == "F2":
        just["texto"] += f" Fundamenta-se ainda no art. {artigo_livre(194)} da Lei nº 14.133/2021."
    elif tipo == "F3":
        # numero de artigo presente no contexto, atribuido a outra norma
        candidatos = []
        for c in contexto:
            propria = next((v for k, v in norma_ctx.items() if c["referencia"].startswith(k)), None)
            num = c["referencia"].split("art. ")[-1].split(",")[0].replace("º", "")
            if propria and num.isdigit():
                outra = "10947" if propria != "10947" else "14133"
                limite = 24 if outra == "10947" else 194
                if int(num) <= limite and (outra, num) not in pares:
                    candidatos.append((num, outra))
        if not candidatos:
            return None
        num, outra = rnd.choice(candidatos)
        nome = "do Decreto nº 10.947/2022" if outra == "10947" else "da Lei nº 14.133/2021"
        just["texto"] += f" Observa-se o art. {num} {nome}."
    elif tipo == "F4":
        just["texto"] += " Observa-se, ainda, o disposto na Lei nº 8.666/1993."
    elif tipo == "F5":
        a, b = sorted(rnd.sample([n for n in range(1, 195) if str(n) not in numeros], 2))
        just["texto"] += f" Aplicam-se os arts. {a} e {b} da Lei nº 14.133/2021."
    elif tipo == "F6":
        m["descricao_objeto"]["fundamentos"] = []
    elif tipo == "F7":
        m["quantidade"] = {"valor": "25 unidades", "evidencia": "25 unidades", "origem": "modelo"}
    elif tipo == "F8":
        valor = (demanda.get("campos") or {}).get("quantidade")
        if not valor:
            return None
        m["quantidade"] = {"valor": valor, "evidencia": f"aproximadamente {valor} itens",
                           "origem": "modelo"}
    return m


def e4_verificacao(recuperador: Recuperador, unidades: list[Unidade], semente: int = 7
                   ) -> dict[str, Any]:
    import app.core.verificacao as v03

    v020 = importlib.import_module("avaliacao.baseline.verificacao_v020")
    texto = {u.ident: (u.citacao, u.texto_indexado) for u in unidades}
    g = gabarito()
    rnd = random.Random(semente)
    linhas: list[dict[str, Any]] = []
    for d in g["demandas"]:
        consulta = _consulta(d["descricao"])
        ids = [i for i, _ in recuperador.buscar(consulta, K_SISTEMA)]
        contexto = [
            {"marcador": f"T{n}", "referencia": texto[i][0], "texto": texto[i][1]}
            for n, i in enumerate(ids, start=1)
        ]
        base = _minuta_base(consulta, contexto)
        for tipo in TIPOS_FALHA:
            minuta = _injetar(tipo, base, contexto, rnd, d)
            if minuta is None:
                continue
            for versao, modulo in (("v0.2.0", v020), ("v0.3", v03)):
                rel = modulo.verificar(minuta, contexto, consulta)
                linhas.append({"id": d["id"], "tipo": tipo, "versao": versao,
                               "detectado": bool(rel.inconsistencia_detectada)})
    resumo: list[dict[str, Any]] = []
    for tipo, rotulo in TIPOS_FALHA.items():
        item: dict[str, Any] = {"tipo": tipo, "descricao": rotulo}
        for versao in ("v0.2.0", "v0.3"):
            casos = [ln for ln in linhas if ln["tipo"] == tipo and ln["versao"] == versao]
            item["casos"] = len(casos)
            item[versao] = round(sum(ln["detectado"] for ln in casos) / len(casos), 4) if casos else None
        resumo.append(item)
    return {"resumo": resumo, "linhas": linhas}


# ======================================================================= E5
def _aplicar_minimizacao(f, conjunto: dict) -> dict[str, Any]:  # noqa: ANN001
    por_tipo: dict[str, list[int]] = {}
    preservados = preservar_total = 0
    for caso in conjunto["positivos"]:
        saida = f(caso["texto"])
        for valor in caso["remover"]:
            tipo = caso["tipo"] if caso["tipo"] != "MISTO" else _tipo_valor(valor)
            por_tipo.setdefault(tipo, []).append(int(valor not in saida))
        for valor in caso.get("preservar", []):
            preservar_total += 1
            preservados += int(valor in saida)
    fps = [c["id"] for c in conjunto["negativos"] if f(c["texto"]) != c["texto"]]
    itens = [x for v in por_tipo.values() for x in v]
    return {
        "revocacao_por_tipo": {t: round(sum(v) / len(v), 4) for t, v in sorted(por_tipo.items())},
        "itens_por_tipo": {t: len(v) for t, v in sorted(por_tipo.items())},
        "revocacao_global": round(sum(itens) / len(itens), 4),
        "itens": len(itens),
        "preservacao": round(preservados / preservar_total, 4) if preservar_total else None,
        "negativos": len(conjunto["negativos"]),
        "falsos_positivos": len(fps),
        "falsos_positivos_ids": fps,
    }


def _tipo_valor(valor: str) -> str:
    if "@" in valor:
        return "EMAIL"
    if re.fullmatch(r"[\d.\-]+", valor) and len(re.sub(r"\D", "", valor)) == 11:
        return "CPF"
    if re.search(r"\d{4}-\d{4}", valor):
        return "TELEFONE"
    return "NOME"


def e5_minimizacao() -> dict[str, Any]:
    from app.core import anonimizador as v03

    v020 = importlib.import_module("avaliacao.baseline.anonimizador_v020")
    versoes = {
        "v0.2.0": lambda t: v020.minimizar(t).texto,
        "v0.3 (regex)": lambda t: v03.minimizar(t, nomes=False).texto,
        "v0.3 (regex + NER)": lambda t: v03.minimizar(t).texto,
    }
    saida: dict[str, Any] = {"ner_disponivel": v03.ner_disponivel(), "conjuntos": {}}
    for nome_conj, arquivo in (("desenvolvimento", "dados_pessoais.yaml"),
                               ("teste", "dados_pessoais_teste.yaml")):
        conjunto = yaml.safe_load((RAIZ_AVALIACAO / "dados" / arquivo).read_text(encoding="utf-8"))
        saida["conjuntos"][nome_conj] = {
            versao: _aplicar_minimizacao(f, conjunto) for versao, f in versoes.items()
        }
        saida["conjuntos"][nome_conj]["_tamanho"] = {
            "positivos": len(conjunto["positivos"]), "negativos": len(conjunto["negativos"])}
    return saida


# =================================================================== API (E6/E7/E9)
class ProvedorEspiao:
    """Provedor deterministico que registra o prompt recebido (CT04) ou falha (CT03)."""

    nome = "espiao"

    def __init__(self, falhar: bool = False) -> None:
        from app.llm.provedores import ProvedorEco

        self._eco = ProvedorEco()
        self.falhar = falhar
        self.prompts: list[str] = []

    def completar(self, prompt_sistema: str, prompt_usuario: str, *, formato_json: bool = True):  # noqa: ANN201
        from app.llm.base import ErroProvedorLLM

        self.prompts.append(prompt_usuario)
        if self.falhar:
            raise ErroProvedorLLM("indisponibilidade simulada (cenário A2)")
        return self._eco.completar(prompt_sistema, prompt_usuario)


def preparar_api(embedder: Any) -> dict[str, Any]:
    """Banco temporario, usuarios e corpus oficial ingerido; mede a ingestao."""
    from app.core.embeddings import redefinir_embedder
    from app.core.indice import redefinir_indice
    from app.database import aplicar_esquema, sessao_escopo
    from app.models import Usuario
    from app.security import gerar_hash_senha
    from app.services.ingestao import ingerir_arquivo
    from .corpus import OFICIAL

    redefinir_embedder(embedder)
    redefinir_indice(None)
    aplicar_esquema()
    with sessao_escopo() as s:
        for nome, email, perfil, senha in (
            ("Agente Demandante", "demandante@orgao.gov.br", "demandante", "demandante123"),
            ("Outro Demandante", "outro@orgao.gov.br", "demandante", "outro12345"),
            ("Gestor da Base", "gestor@orgao.gov.br", "gestor", "gestor123"),
        ):
            s.add(Usuario(nome=nome, email=email, perfil=perfil,
                          senha_hash=gerar_hash_senha(senha), unidade="Unidade de teste"))
    ingestao = []
    for sigla, tipo, numero, ano in (("L14133", "Lei", "14.133", 2021),
                                     ("D10947", "Decreto", "10.947", 2022),
                                     ("L13709", "Lei", "13.709", 2018)):
        inicio = time.perf_counter()
        with sessao_escopo() as s:
            r = ingerir_arquivo(s, OFICIAL / f"{sigla}.htm", sigla=sigla, tipo=tipo,
                                numero=numero, ano=ano, ementa=f"{tipo} {numero}")
        ingestao.append({"sigla": sigla, "dispositivos": r.dispositivos,
                         "segundos": round(time.perf_counter() - inicio, 2)})
    return {"ingestao": ingestao, "gerador": embedder.identificador}


def _cliente():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _entrar(cli, email: str, senha: str) -> dict[str, str]:  # noqa: ANN001
    r = cli.post("/auth/login", json={"email": email, "senha": senha})
    return {"Authorization": f"Bearer {r.json()['token']}"}


CAMPOS = ["justificativa_necessidade", "descricao_objeto", "quantidade", "estimativa_valor",
          "data_pretendida", "grau_prioridade", "vinculacao_outro_dfd", "area_requisitante"]


def _demanda(d: dict[str, Any]) -> dict[str, str]:
    return {"unidade_requisitante": "Secretaria de Administração",
            "titulo": d.get("titulo") or "Demanda de teste", "descricao": d["descricao"]}


# ======================================================================= E6
def e6_desempenho(repeticoes: int = 1) -> dict[str, Any]:
    """Percorre o cenario principal para cada demanda do gabarito, medindo cada passo."""
    from app.llm.factory import redefinir_provedor
    from app.llm.provedores import ProvedorEco

    redefinir_provedor(ProvedorEco())
    linhas: list[dict[str, Any]] = []
    with _cliente() as cli:
        cab = _entrar(cli, "demandante@orgao.gov.br", "demandante123")
        for _ in range(repeticoes):
            for d in gabarito()["demandas"]:
                t: dict[str, Any] = {"id": d["id"]}
                inicio = time.perf_counter()
                r = cli.post("/demandas", json=_demanda(d), headers=cab)
                t["registrar_ms"] = (time.perf_counter() - inicio) * 1000
                id_dfd = r.json()["id_dfd"]
                inicio = time.perf_counter()
                r = cli.post(f"/dfd/{id_dfd}/gerar", json={}, headers=cab)
                t["gerar_ms"] = (time.perf_counter() - inicio) * 1000
                if r.status_code != 200:
                    t["gerar_status"] = r.status_code
                    linhas.append(t)
                    continue
                minuta = r.json()
                for etapa, valor in (minuta.get("desempenho") or {}).items():
                    t[f"etapa_{etapa}_ms"] = valor
                inicio = time.perf_counter()
                r = cli.post(f"/dfd/{id_dfd}/validar", headers=cab, json={
                    "id_minuta": minuta["id_minuta"], "decisao": "aprovado",
                    "campos_confirmados": CAMPOS})
                t["validar_ms"] = (time.perf_counter() - inicio) * 1000
                inicio = time.perf_counter()
                r = cli.post(f"/dfd/{id_dfd}/exportar", headers=cab)
                t["exportar_ms"] = (time.perf_counter() - inicio) * 1000
                t["exportar_status"] = r.status_code
                linhas.append(t)
    chaves = sorted({k for ln in linhas for k in ln if k.endswith("_ms")})
    resumo = []
    for k in chaves:
        valores = [ln[k] for ln in linhas if k in ln]
        resumo.append({"medida": k, "n": len(valores),
                       "mediana_ms": round(statistics.median(valores), 1),
                       "p95_ms": round(M.percentil(valores, 95), 1),
                       "max_ms": round(max(valores), 1)})
    return {"resumo": resumo, "linhas": linhas,
            "observacao": "provedor deterministico (echo): o tempo do LLM nao esta incluido"}


# ======================================================================= E7
def e7_cenarios() -> dict[str, Any]:
    """Cenarios funcionais da secao 3.4.2, pela fronteira HTTP."""
    from app.llm.factory import redefinir_provedor

    g = gabarito()
    casos: list[dict[str, Any]] = []

    def registrar(cid: str, nome: str, esperado: str, obtido: str, ok: bool, evid: str) -> None:
        casos.append({"id": cid, "cenario": nome, "esperado": esperado, "obtido": obtido,
                      "aprovado": bool(ok), "evidencia": evid})

    espiao = ProvedorEspiao()
    redefinir_provedor(espiao)
    try:
        _cenarios(g, casos, registrar, espiao)
    finally:
        redefinir_provedor(None)
    return {"casos": casos, "aprovados": sum(c["aprovado"] for c in casos), "total": len(casos)}


def _cenarios(g, casos, registrar, espiao) -> None:  # noqa: ANN001, C901
    from app.config import config
    from app.core.auditoria import verificar_cadeia

    def corpo(resposta) -> dict:  # noqa: ANN001
        try:
            return resposta.json() if isinstance(resposta.json(), dict) else {}
        except ValueError:
            return {}

    with _cliente() as cli:
        cab = _entrar(cli, "demandante@orgao.gov.br", "demandante123")
        gestor = _entrar(cli, "gestor@orgao.gov.br", "gestor123")
        outro = _entrar(cli, "outro@orgao.gov.br", "outro12345")

        # CT01 — cenario principal --------------------------------------------
        d = g["demandas"][0]
        codigos = []
        r = cli.post("/demandas", json=_demanda(d), headers=cab); codigos.append(r.status_code)
        id_dfd = r.json()["id_dfd"]
        r = cli.post(f"/dfd/{id_dfd}/gerar", json={}, headers=cab); codigos.append(r.status_code)
        v1 = corpo(r)
        if "conteudo" not in v1:
            registrar("CT01", "Cenário principal (registro → exportação)",
                      "201, 200, 200, 200, 200", ", ".join(map(str, codigos)), False,
                      (v1.get("detail") or "")[:90])
            return
        conteudo = copy.deepcopy(v1["conteudo"])
        conteudo["justificativa_necessidade"]["texto"] += " Revisado pelo agente público."
        r = cli.post(f"/dfd/{id_dfd}/revisar", json={"conteudo": conteudo}, headers=cab)
        codigos.append(r.status_code); v2 = r.json()
        r = cli.post(f"/dfd/{id_dfd}/validar", headers=cab, json={
            "id_minuta": v2["id_minuta"], "decisao": "aprovado", "campos_confirmados": CAMPOS})
        codigos.append(r.status_code)
        r = cli.post(f"/dfd/{id_dfd}/exportar", headers=cab); codigos.append(r.status_code)
        exp = r.json()
        arq = cli.get(exp["url_download"], headers=cab)
        import hashlib
        hash_ok = hashlib.sha256(arq.content).hexdigest() == exp.get("hash_arquivo")
        acoes = [x["acao"] for x in cli.get(f"/auditoria?id_dfd={id_dfd}", headers=cab).json()]
        cadeia = cli.get("/auditoria/integridade", headers=gestor).json()
        ok = codigos == [201, 200, 200, 200, 200] and hash_ok and cadeia["integra"] and {
            "REGISTRO_DEMANDA", "GERACAO", "EDICAO", "VALIDACAO", "EXPORTACAO"} <= set(acoes)
        registrar("CT01", "Cenário principal (registro → exportação)",
                  "201, 200, 200, 200, 200; .docx com hash conferido; auditoria íntegra",
                  f"{', '.join(map(str, codigos))}; hash {'conferido' if hash_ok else 'divergente'}; "
                  f"cadeia {'íntegra' if cadeia['integra'] else 'quebrada'} ({cadeia['total']} registros)",
                  ok, f"ações: {', '.join(sorted(set(acoes)))}")

        # CT02 — A1: demanda fora do dominio ----------------------------------
        chamadas_antes = len(espiao.prompts)
        fora = {"descricao": g["fora_do_dominio"][1]["descricao"], "titulo": "Fora do domínio"}
        id_a1 = cli.post("/demandas", json=_demanda(fora), headers=cab).json()["id_dfd"]
        r = cli.post(f"/dfd/{id_a1}/gerar", json={}, headers=cab)
        acoes = [x["acao"] for x in cli.get(f"/auditoria?id_dfd={id_a1}", headers=cab).json()]
        ok = r.status_code == 422 and len(espiao.prompts) == chamadas_antes and "GERACAO_BARRADA" in acoes
        registrar("CT02", "A1 — demanda sem fundamento normativo",
                  "422; nenhuma chamada ao LLM; GERACAO_BARRADA auditada",
                  f"{r.status_code}; chamadas ao LLM: {len(espiao.prompts) - chamadas_antes}; "
                  f"{'GERACAO_BARRADA registrada' if 'GERACAO_BARRADA' in acoes else 'sem registro'}",
                  ok, (r.json().get("detail") or "")[:90])

        # CT03 — A2: LLM indisponivel -----------------------------------------
        espiao.falhar = True
        id_a2 = cli.post("/demandas", json=_demanda(g["demandas"][1]), headers=cab).json()["id_dfd"]
        r = cli.post(f"/dfd/{id_a2}/gerar", json={}, headers=cab)
        espiao.falhar = False
        preservada = cli.get(f"/demandas/{id_a2}", headers=cab).status_code == 200
        acoes = [x["acao"] for x in cli.get(f"/auditoria?id_dfd={id_a2}", headers=cab).json()]
        ok = r.status_code == 503 and preservada and "FALHA_GERACAO" in acoes
        registrar("CT03", "A2 — serviço de LLM indisponível",
                  "503; demanda preservada; FALHA_GERACAO auditada",
                  f"{r.status_code}; demanda {'preservada' if preservada else 'perdida'}; "
                  f"{'FALHA_GERACAO registrada' if 'FALHA_GERACAO' in acoes else 'sem registro'}",
                  ok, (r.json().get("detail") or "")[:90])

        # CT04 — dados pessoais -----------------------------------------------
        pii = ["529.982.247-25", "fernanda.ribeiro@educacao.foz.pr.gov.br", "(45) 99123-4567",
               "Fernanda Costa Ribeiro"]
        com_pii = dict(_demanda(g["demandas"][0]))
        com_pii["descricao"] += (f" Responsável: {pii[3]}, CPF {pii[0]}, e-mail {pii[1]}, "
                                 f"telefone {pii[2]}.")
        r = cli.post("/demandas", json=com_pii, headers=cab)
        gravada = r.json()["descricao"]
        removidos = r.json()["dados_pessoais_removidos"]
        id_pii = r.json()["id_dfd"]
        n_antes = len(espiao.prompts)
        cli.post(f"/dfd/{id_pii}/gerar", json={}, headers=cab)
        prompt = espiao.prompts[-1] if len(espiao.prompts) > n_antes else ""
        vazou_banco = [p for p in pii if p in gravada]
        vazou_prompt = [p for p in pii if p in prompt]
        esperado_nome = config.ANONIMIZADOR_NER
        ok = not vazou_prompt and not vazou_banco and bool(prompt)
        registrar("CT04", "Dados pessoais na descrição da demanda",
                  "marcadores no texto gravado e no prompt enviado ao LLM",
                  f"removidos {removidos}; vazamentos no banco: {len(vazou_banco)}; "
                  f"no prompt: {len(vazou_prompt)}",
                  ok, f"NER {'ativo' if esperado_nome else 'inativo'}")

        # CT05 — exportacao sem validacao (API e SQL direto) -------------------
        id_ct5 = cli.post("/demandas", json=_demanda(g["demandas"][2]), headers=cab).json()["id_dfd"]
        m5 = corpo(cli.post(f"/dfd/{id_ct5}/gerar", json={}, headers=cab))
        if "id_minuta" not in m5:
            registrar("CT05", "Exportação sem validação", "minuta gerada", "geração falhou", False,
                      (m5.get("detail") or "")[:90])
            return
        r = cli.post(f"/dfd/{id_ct5}/exportar", headers=cab)
        bloqueio_sql = False
        conexao = sqlite3.connect(config.DB_ARQUIVO)
        try:
            conexao.execute(
                "INSERT INTO exportacao (id_dfd, id_minuta, id_usuario, formato, data_hora) "
                "VALUES (?, ?, 1, 'docx', datetime('now'))", (id_ct5, m5["id_minuta"]))
            conexao.commit()
        except sqlite3.DatabaseError as erro:
            bloqueio_sql = "RN01" in str(erro)
        finally:
            conexao.close()
        registrar("CT05", "Exportação sem validação (API e SQL direto)",
                  "409 na API; INSERT direto rejeitado pelo gatilho RN01",
                  f"{r.status_code}; SQL direto {'rejeitado (RN01)' if bloqueio_sql else 'ACEITO'}",
                  r.status_code == 409 and bloqueio_sql, "gatilho trg_rn01_validacao")

        # CT06 — aprovacao sem confirmar todos os campos (RF07) ---------------
        r = cli.post(f"/dfd/{id_ct5}/validar", headers=cab, json={
            "id_minuta": m5["id_minuta"], "decisao": "aprovado", "campos_confirmados": CAMPOS[:7]})
        registrar("CT06", "Aprovação com 7 de 8 campos confirmados (RF07)", "422",
                  str(r.status_code), r.status_code == 422, (r.json().get("detail") or "")[:90])

        # CT07 — aprovacao nao se transfere para versao editada ---------------
        cli.post(f"/dfd/{id_ct5}/validar", headers=cab, json={
            "id_minuta": m5["id_minuta"], "decisao": "aprovado", "campos_confirmados": CAMPOS})
        conteudo = copy.deepcopy(m5["conteudo"])
        conteudo["descricao_objeto"]["texto"] += " Alteração após a aprovação."
        cli.post(f"/dfd/{id_ct5}/revisar", json={"conteudo": conteudo}, headers=cab)
        r = cli.post(f"/dfd/{id_ct5}/exportar", headers=cab)
        registrar("CT07", "Edição após a aprovação, seguida de exportação",
                  "409 (a aprovação vale só para a versão aprovada)", str(r.status_code),
                  r.status_code == 409, "RN01: versão exata")

        # CT08 — acesso de outro demandante -----------------------------------
        r = cli.get(f"/dfd/{id_ct5}", headers=outro)
        registrar("CT08", "Outro demandante consulta DFD alheio", "404 (não revela existência)",
                  str(r.status_code), r.status_code == 404, "RNF05")

        # CT09 — demandante tenta gerar sem RAG -------------------------------
        r = cli.post(f"/dfd/{id_ct5}/gerar", json={"usar_rag": False}, headers=cab)
        registrar("CT09", "Demandante solicita geração sem RAG", "403 (restrito aos experimentos)",
                  str(r.status_code), r.status_code == 403, "RN03")

        # CT10 — adulteracao da auditoria -------------------------------------
        conexao = sqlite3.connect(config.DB_ARQUIVO)
        try:
            conexao.execute("UPDATE registro_auditoria SET acao = 'X' WHERE id_registro = 1")
            update_bloqueado = False
        except sqlite3.DatabaseError:
            update_bloqueado = True
        finally:
            conexao.close()
        copia = Path(tempfile.mkdtemp()) / "copia.sqlite3"
        origem = sqlite3.connect(config.DB_ARQUIVO)
        destino = sqlite3.connect(copia)
        origem.backup(destino)
        origem.close()
        destino.execute("DROP TRIGGER trg_auditoria_sem_update")
        destino.execute("UPDATE registro_auditoria SET detalhe = '{\"adulterado\": true}' "
                        "WHERE id_registro = 3")
        destino.commit()
        destino.close()
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        with Session(create_engine(f"sqlite:///{copia}")) as s:
            laudo = verificar_cadeia(s)
        registrar("CT10", "Adulteração direta da trilha de auditoria",
                  "UPDATE rejeitado; adulteração com gatilho removido detectada pela cadeia de hashes",
                  f"UPDATE {'rejeitado' if update_bloqueado else 'ACEITO'}; cadeia "
                  f"{'íntegra' if laudo['integra'] else 'quebrada no registro ' + str(laudo['primeiro_divergente'])}",
                  update_bloqueado and not laudo["integra"] and laudo["primeiro_divergente"] == 3,
                  "RN05, RNF07")


# ======================================================================= E8
GRUPOS_TESTE = {
    "test_segmentador.py": "Segmentação normativa (F1)",
    "test_nucleo.py": "Minimização e verificação (RN02, RN06)",
    "test_verificacao.py": "Verificação com norma qualificada (RN02)",
    "test_anonimizador.py": "Minimização v0.3 (RN06)",
    "test_governanca.py": "Governança na camada de dados (RN01, RN05)",
    "test_fluxo.py": "Fluxo pela API (UC01–UC11)",
    "test_lexical.py": "Índice lexical e modos de recuperação (RF03)",
    "test_provedores.py": "Provedores de modelo de linguagem (RNF08)",
    "test_avaliacao_e9.py": "Roteiro do E9 (métricas, retomada e cache)",
    "test_correcao.py": "Revisão de redação por segundo modelo (UC05)",
}


def e8_testes(raiz_backend: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        cobertura = Path(tmp) / "cov.json"
        inicio = time.perf_counter()
        processo = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}",
             "--cov=app", f"--cov-report=json:{cobertura}", "-p", "no:cacheprovider"],
            cwd=raiz_backend, capture_output=True, text=True, timeout=900,
        )
        duracao = time.perf_counter() - inicio
        raiz = ET.parse(junit).getroot()
        por_arquivo: dict[str, dict[str, int]] = {}
        for caso in raiz.iter("testcase"):
            arquivo = (caso.get("classname") or "").split(".")[-1] + ".py"
            item = por_arquivo.setdefault(arquivo, {"testes": 0, "falhas": 0, "ignorados": 0})
            item["testes"] += 1
            if caso.find("failure") is not None or caso.find("error") is not None:
                item["falhas"] += 1
            if caso.find("skipped") is not None:
                item["ignorados"] += 1
        cov = json.loads(cobertura.read_text())
        modulos = {
            nome.replace("\\", "/"): round(dados["summary"]["percent_covered"], 1)
            for nome, dados in cov["files"].items()
        }
    return {
        "codigo_saida": processo.returncode,
        "duracao_s": round(duracao, 1),
        "por_arquivo": [
            {"arquivo": a, "grupo": GRUPOS_TESTE.get(a, a), **v} for a, v in sorted(por_arquivo.items())
        ],
        "total": sum(v["testes"] for v in por_arquivo.values()),
        "falhas": sum(v["falhas"] for v in por_arquivo.values()),
        "ignorados": sum(v["ignorados"] for v in por_arquivo.values()),
        "cobertura_total": round(cov["totals"]["percent_covered"], 1),
        "cobertura_modulos": modulos,
        "saida": processo.stdout[-400:],
    }


# ======================================================================= E9
# Condicao "LLM isolada": o que um servidor obteria colando a demanda num
# assistente de IA, sem nada do sistema (sem minimizacao, recuperacao, portao,
# esquema de saida nem verificacao). Pede-se o mesmo conteudo do DFD e a
# fundamentacao legal, para que as metricas sejam comparaveis.
PROMPT_ISOLADO_SISTEMA = (
    "Você é um servidor público que elabora documentos de planejamento de contratações públicas."
)
PROMPT_ISOLADO_USUARIO = (
    "Elabore o Documento de Formalização da Demanda (DFD) para a necessidade abaixo, conforme a "
    "Lei nº 14.133/2021 e o Decreto nº 10.947/2022, com: justificativa da necessidade, descrição "
    "do objeto, quantidade, estimativa de valor, data pretendida e grau de prioridade. Cite os "
    "dispositivos legais que fundamentam a contratação.\n\nNecessidade: {descricao}"
)
# condicao -> (rotulo, parametros da geracao pela API; None = LLM isolada)
CONDICOES_E9: dict[str, tuple[str, dict[str, Any] | None]] = {
    "completo": ("Sistema completo (RAG)", {"usar_rag": True, "modo": None}),
    "completo_bm25": ("Sistema com RAG só lexical (BM25)", {"usar_rag": True, "modo": "lexical"}),
    "sem_rag": ("Sistema sem RAG", {"usar_rag": False, "modo": None}),
    "isolada": ("LLM isolada", None),
}
CONDICOES_PADRAO = ("completo", "sem_rag", "isolada")
_NORMAS_E9 = {"14133": "L14133", "10947": "D10947", "13709": "L13709"}
# Normas revogadas pela Lei nº 14.133/2021 (art. 193): cita-las e erro juridico,
# ainda que o artigo exista na norma antiga.
NORMAS_REVOGADAS = {"8666": "Lei nº 8.666/1993", "10520": "Lei nº 10.520/2002",
                    "12462": "Lei nº 12.462/2011"}
# Situacao final de uma linha: nao e refeita na retomada.
STATUS_FINAIS = {200, 422}


def modelo_configurado(provedor: str) -> str:
    """Modelo que o provedor usara, segundo a configuracao (chave do checkpoint)."""
    from app.config import config

    return {"anthropic": config.LLM_MODELO, "gemini": config.GEMINI_MODELO,
            "groq": config.GROQ_MODELO, "ollama": config.OLLAMA_MODELO}.get(provedor, provedor)


def amostra_demandas(n: int | None = None, ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Demandas do gabarito: todas, as indicadas por id ou n espacadas uniformemente.

    A amostra espacada (D01, D06, D12...) cobre os varios tipos de objeto do
    gabarito sem escolha manual, e e a mesma em toda execucao.
    """
    demandas = gabarito()["demandas"]
    if ids:
        pedidas = {i.strip().upper() for i in ids}
        return [d for d in demandas if d["id"].upper() in pedidas]
    if n and n < len(demandas):
        passo = len(demandas) / n
        return [demandas[int(i * passo)] for i in range(n)]
    return demandas


def _texto_comparavel(texto: str) -> str:
    """Minusculas, sem acento e so com letras e digitos ("R$ 1.000,00" -> "r100000")."""
    from app.core.verificacao import normalizar

    return re.sub(r"\W", "", normalizar(texto))


def _citacoes_e9(texto: str, existentes: set[tuple[str, str]],
                 relevantes: set[tuple[str, str]]) -> dict[str, Any]:
    """Conta as mencoes a artigo e as classifica contra a base e o gabarito.

    Mencao sem norma ("art. 18") vale para qualquer das tres normas da base: e
    inexistente so se o artigo nao existir em nenhuma, e pertinente se coincidir
    com um relevante de qualquer uma. Norma fora da base nao e pertinente nem
    verificavel; se for uma norma revogada pela Lei nº 14.133/2021 (Lei nº
    8.666/1993, por exemplo), a citacao e contada a parte, como erro juridico.
    """
    from app.core.verificacao import mencoes, normas_mencionadas

    total = inexistentes = pertinentes = revogadas = 0
    for artigo, norma in mencoes(texto):
        artigo = artigo.split("-")[0]
        if norma in _NORMAS_E9:
            siglas = [_NORMAS_E9[norma]]
        else:
            siglas = [] if norma else list(_NORMAS_E9.values())
        total += 1
        revogadas += norma in NORMAS_REVOGADAS
        if siglas and not any((s, artigo) in existentes for s in siglas):
            inexistentes += 1
        if any((s, artigo) in relevantes for s in siglas):
            pertinentes += 1
    return {"citacoes": total, "citacoes_inexistentes": inexistentes,
            "citacoes_pertinentes": pertinentes, "citacoes_norma_revogada": revogadas,
            "menciona_norma_revogada": bool(normas_mencionadas(texto) & set(NORMAS_REVOGADAS))}


def _valores_presentes(texto: str, campos: dict[str, Any]) -> float | None:
    """Fracao dos valores esperados da demanda (gabarito) que aparecem no texto."""
    from app.core.verificacao import normalizar

    esperados = [(c, v) for c, v in (campos or {}).items() if v]
    if not esperados:
        return None
    alvo, alvo_palavras = _texto_comparavel(texto), normalizar(texto)
    acertos = 0
    for campo, valor in esperados:
        if campo == "grau_prioridade":   # "alto" casa com "alta"; "medio" com "media"
            raiz = _texto_comparavel(valor)[:-1]
            acertos += bool(re.search(r"prioridade\W+(?:\w+\W+){0,3}?" + raiz, alvo_palavras))
        else:
            acertos += _texto_comparavel(valor) in alvo
    return round(acertos / len(esperados), 4)


def _ultimo_erro_auditado(id_dfd: int) -> str | None:
    """Causa real de uma falha de geracao: a API devolve so a mensagem generica (A2)."""
    from sqlalchemy import select

    from app.database import sessao_escopo
    from app.models import RegistroAuditoria

    with sessao_escopo() as s:
        reg = s.scalars(
            select(RegistroAuditoria)
            .where(RegistroAuditoria.id_dfd == id_dfd, RegistroAuditoria.acao == "FALHA_GERACAO")
            .order_by(RegistroAuditoria.id_registro.desc())
        ).first()
        return str((reg.detalhe or {}).get("erro")) if reg else None


def _tokens_da_minuta(id_minuta: int) -> dict[str, int]:
    from app.database import sessao_escopo
    from app.models import Minuta

    with sessao_escopo() as s:
        minuta = s.get(Minuta, id_minuta)
        parametros = (minuta.parametros or {}) if minuta else {}
    return {k: parametros[k] for k in ("tokens_entrada", "tokens_saida", "tokens_raciocinio")
            if parametros.get(k) is not None}


def _ler_checkpoint(arquivo: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if not arquivo or not arquivo.exists():
        return {}
    linhas: dict[tuple[str, str], dict[str, Any]] = {}
    for bruta in arquivo.read_text(encoding="utf-8").splitlines():
        if bruta.strip():
            ln = json.loads(bruta)
            linhas[(ln["id"], ln["condicao"])] = ln   # a ultima tentativa prevalece
    return linhas


def _gravar_checkpoint(arquivo: Path | None, ln: dict[str, Any]) -> None:
    if arquivo:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        with arquivo.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ln, ensure_ascii=False, default=str) + "\n")


def _linha_e9(cli, gestor: dict[str, str], provedor: str, d: dict[str, Any],  # noqa: ANN001
              condicao: str, existentes: set[tuple[str, str]]) -> dict[str, Any]:
    """Gera a minuta de uma demanda numa condicao e mede o texto produzido."""
    from app.config import config
    from app.llm.base import ErroProvedorLLM
    from app.llm.factory import obter_provedor

    campos = d.get("campos") or {}
    relevantes = {tuple(artigo_de(r).replace("º", "").split("|")) for r in d.get("relevantes", [])}
    _rotulo, geracao = CONDICOES_E9[condicao]
    ln: dict[str, Any] = {"id": d["id"], "condicao": condicao}
    texto: str | None = None
    inicio = time.perf_counter()
    if geracao is None:   # LLM isolada
        try:
            resp = obter_provedor(provedor).completar(
                PROMPT_ISOLADO_SISTEMA, PROMPT_ISOLADO_USUARIO.format(descricao=d["descricao"]),
                formato_json=False,
            )
            ln.update(status=200, modelo=resp.modelo, texto=resp.texto)
            ln.update({k: resp.parametros[k] for k in ("tokens_entrada", "tokens_saida",
                                                       "tokens_raciocinio")
                       if resp.parametros.get(k) is not None})
            texto = resp.texto
        except ErroProvedorLLM as erro:
            ln.update(status=502, erro=str(erro)[:300])
        ln["latencia_s"] = round(time.perf_counter() - inicio, 2)
    else:
        modo_original = config.RECUPERACAO_MODO
        if geracao["modo"]:
            config.RECUPERACAO_MODO = geracao["modo"]
        try:
            id_dfd = cli.post("/demandas", json=_demanda(d), headers=gestor).json()["id_dfd"]
            r = cli.post(f"/dfd/{id_dfd}/gerar", headers=gestor,
                         json={"provedor": provedor, "usar_rag": geracao["usar_rag"]})
        finally:
            config.RECUPERACAO_MODO = modo_original
        ln["status"] = r.status_code
        ln["latencia_s"] = round(time.perf_counter() - inicio, 2)
        if r.status_code == 200:
            m = r.json()
            ver = m["verificacao"]
            por_campo = ver.get("por_campo", {})
            ln.update(
                modelo=m.get("modelo_llm"),
                ancoragem=ver.get("taxa_ancoragem"),
                inconsistencia=ver.get("inconsistencia_detectada"),
                normas_fora=sum(len(p.get("normas_fora_do_contexto", [])) for p in por_campo.values()),
                extracao_sem_evidencia=sum(bool(p.get("extracao_sem_evidencia"))
                                           for p in por_campo.values()),
                fontes=[f.get("referencia") for f in m.get("fontes", [])],
                **_tokens_da_minuta(m["id_minuta"]),
            )
            acertos = 0
            for campo, esperado in campos.items():
                obtido = (m["conteudo"].get(campo) or {}).get("valor")
                if esperado is None:
                    acertos += obtido is None
                else:
                    acertos += bool(obtido) and (_texto_comparavel(esperado)
                                                 in _texto_comparavel(str(obtido)))
            ln["extracao_correta"] = round(acertos / len(campos), 4) if campos else None
            conteudo = m["conteudo"]
            texto = "\n".join(
                [(conteudo.get(c) or {}).get("texto") or ""
                 for c in ("justificativa_necessidade", "descricao_objeto")]
                + [str((conteudo.get(c) or {}).get("valor") or "") for c in campos]
            )
            ln["texto"] = texto
        elif r.status_code == 422:
            ln["erro"] = "barrada pelo portão de suficiência (fluxo A1)"
        else:
            ln["erro"] = (_ultimo_erro_auditado(id_dfd) or r.text)[:300]
    if texto is not None:
        ln.update(_citacoes_e9(texto, existentes, relevantes))
        ln["valores_presentes"] = _valores_presentes(texto, campos)
    return ln


def _por_minuta(ln: dict[str, Any], metrica: str) -> float | None:
    """Metricas por minuta usadas nas comparacoes pareadas."""
    citacoes = ln.get("citacoes") or 0
    if metrica == "precisao_citacoes":
        return ln["citacoes_pertinentes"] / citacoes if citacoes else None
    if metrica == "taxa_inexistentes":
        return ln["citacoes_inexistentes"] / citacoes if citacoes else None
    valor = ln.get(metrica)
    return None if valor is None else float(valor)


def _mediana(valores: list[float]) -> float | None:
    return round(statistics.median(valores), 1) if valores else None


def _resumo_e9(linhas: list[dict[str, Any]], condicoes: list[str]) -> list[dict[str, Any]]:
    resumo = []
    for condicao in condicoes:
        rotulo, geracao = CONDICOES_E9[condicao]
        todas = [x for x in linhas if x["condicao"] == condicao]
        casos = [x for x in todas if x["status"] == 200]
        if not casos:
            continue
        sistema = geracao is not None
        citacoes = sum(c["citacoes"] for c in casos)
        inexistentes = sum(c["citacoes_inexistentes"] for c in casos)
        valores = [c["valores_presentes"] for c in casos if c.get("valores_presentes") is not None]
        precisoes = [p for c in casos if (p := _por_minuta(c, "precisao_citacoes")) is not None]
        barradas = sum(1 for x in todas if x["status"] == 422)
        falhas_provedor = sum(1 for x in todas if x["status"] not in STATUS_FINAIS)
        resumo.append({
            "condicao": condicao,
            "configuracao": rotulo,
            "n": len(casos),
            "barradas_portao": barradas,
            "falhas_provedor": falhas_provedor,
            "falhas": barradas + falhas_provedor,
            "citacoes_por_minuta": round(citacoes / len(casos), 2),
            "minutas_sem_citacao": sum(1 for c in casos if not c["citacoes"]),
            "pertinentes_por_minuta": round(sum(c["citacoes_pertinentes"] for c in casos) / len(casos), 2),
            "citacoes_pertinentes": (round(sum(c["citacoes_pertinentes"] for c in casos) / citacoes, 4)
                                     if citacoes else None),
            "precisao_macro": round(M.media(precisoes), 4) if precisoes else None,
            "citacoes_inexistentes": inexistentes,
            "citacoes_existentes": round(1 - inexistentes / citacoes, 4) if citacoes else None,
            "citacoes_norma_revogada": sum(c.get("citacoes_norma_revogada", 0) for c in casos),
            "minutas_com_norma_revogada": round(M.media(float(bool(c.get("menciona_norma_revogada")))
                                                        for c in casos), 4),
            "valores_presentes": round(M.media(valores), 4) if valores else None,
            "ancoragem_media": round(M.media(c["ancoragem"] or 0 for c in casos), 4) if sistema else None,
            "com_inconsistencia": (round(M.media(float(bool(c["inconsistencia"])) for c in casos), 4)
                                   if sistema else None),
            "normas_fora": sum(c["normas_fora"] for c in casos) if sistema else None,
            "extracao_correta": (round(M.media(c["extracao_correta"] for c in casos
                                               if c.get("extracao_correta") is not None), 4)
                                 if sistema else None),
            "latencia_mediana_s": round(statistics.median(c["latencia_s"] for c in casos), 2),
            "tokens_entrada_mediana": _mediana([c["tokens_entrada"] for c in casos
                                                if c.get("tokens_entrada")]),
            "tokens_saida_mediana": _mediana([c["tokens_saida"] for c in casos if c.get("tokens_saida")]),
        })
    return resumo


PARES_E9 = (("completo", "isolada"), ("completo", "sem_rag"), ("completo", "completo_bm25"),
            ("sem_rag", "isolada"))
METRICAS_PAREADAS = ("citacoes_pertinentes", "precisao_citacoes", "taxa_inexistentes",
                     "valores_presentes")


def _comparacoes_e9(linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Diferencas pareadas por demanda (A - B) com IC 95% por bootstrap."""
    ok = {(x["id"], x["condicao"]): x for x in linhas if x["status"] == 200}
    saida = []
    for a, b in PARES_E9:
        for metrica in METRICAS_PAREADAS:
            difs = []
            for (ident, cond), ln in ok.items():
                if cond != a or (ident, b) not in ok:
                    continue
                va, vb = _por_minuta(ln, metrica), _por_minuta(ok[(ident, b)], metrica)
                if va is not None and vb is not None:
                    difs.append(va - vb)
            if len(difs) < 3:
                continue
            ic = M.ic_bootstrap(difs)
            saida.append({"a": a, "b": b, "metrica": metrica, "n": len(difs),
                          "diferenca": round(M.media(difs), 4),
                          "ic95": [round(ic[0], 4), round(ic[1], 4)]})
    return saida


def e9_llm(provedor: str, *, condicoes: tuple[str, ...] | list[str] = CONDICOES_PADRAO,
           demandas: list[dict[str, Any]] | None = None, checkpoint: Path | None = None,
           refazer: bool = False, max_falhas_seguidas: int = 3) -> dict[str, Any]:
    """Mesmo modelo e mesmas demandas em varias condicoes (estudo de ablacao).

    * completo: o fluxo do sistema (minimizacao, RAG no modo configurado, portao,
      esquema e verificacao);
    * completo_bm25: o mesmo, com recuperacao so lexical (compara os metodos de
      recuperacao de ponta a ponta);
    * sem_rag: o mesmo fluxo, sem o contexto normativo recuperado;
    * isolada: a LLM sozinha, com um pedido em linguagem natural e texto livre.

    Cada linha e gravada no checkpoint assim que fica pronta. Na reexecucao, as
    linhas concluidas (gerada ou barrada pelo portao) sao reaproveitadas e so as
    que falharam sao refeitas: uma cota diaria esgotada (Gemini gratuito) nao
    perde o que ja foi medido. A execucao para ao primeiro sinal de cota diaria
    ou apos max_falhas_seguidas falhas seguidas do provedor.
    """
    from app.config import config
    from app.core.embeddings import obter_embedder
    from app.core.lexical import modo_efetivo
    from app.llm.factory import redefinir_provedor

    redefinir_provedor(None)
    condicoes = [c for c in condicoes if c in CONDICOES_E9]
    if "completo_bm25" in condicoes and modo_efetivo() == "lexical":
        print("  [aviso] o modo padrão já é lexical (sem BGE-M3): completo_bm25 repetiria o completo")
        condicoes.remove("completo_bm25")
    demandas = demandas or gabarito()["demandas"]
    existentes = {(sig, artigo_de(f"{sig}|{d.referencia}").split("|")[1].replace("º", ""))
                  for sig, lista in dispositivos().items() for d in lista}
    feitas = {} if refazer else _ler_checkpoint(checkpoint)
    if refazer and checkpoint and checkpoint.exists():
        checkpoint.replace(checkpoint.with_suffix(f".{int(time.time())}.bak"))
    linhas: list[dict[str, Any]] = []
    interrompido: str | None = None
    seguidas = 0
    with _cliente() as cli:
        gestor = _entrar(cli, "gestor@orgao.gov.br", "gestor123")
        for d in demandas:
            for condicao in condicoes:
                anterior = feitas.get((d["id"], condicao))
                if anterior and anterior["status"] in STATUS_FINAIS:
                    linhas.append(anterior)
                    continue
                if interrompido:
                    if anterior:
                        linhas.append(anterior)
                    continue
                ln = _linha_e9(cli, gestor, provedor, d, condicao, existentes)
                ln["executado_em"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                _gravar_checkpoint(checkpoint, ln)
                linhas.append(ln)
                print(f"  E9 {provedor} {d['id']} {condicao:13} {ln['status']} "
                      f"{ln['latencia_s']:6.1f} s {ln.get('erro', '')[:70]}", flush=True)
                if ln["status"] in STATUS_FINAIS:
                    seguidas = 0
                    continue
                seguidas += 1
                erro = str(ln.get("erro", ""))
                if "cota diaria" in erro or seguidas >= max_falhas_seguidas:
                    interrompido = erro or f"{seguidas} falhas seguidas do provedor"
                    print(f"  [E9 interrompido] {interrompido}\n  O progresso está salvo em "
                          f"{checkpoint}; rode o mesmo comando depois para continuar.", flush=True)

    pendentes = [(d["id"], c) for d in demandas for c in condicoes
                 if not any(x["id"] == d["id"] and x["condicao"] == c and x["status"] in STATUS_FINAIS
                            for x in linhas)]
    modelos = sorted({str(x["modelo"]) for x in linhas if x.get("modelo")})
    return {
        "provedor": provedor,
        "modelo_configurado": modelo_configurado(provedor),
        "modelos": modelos,
        "recuperacao": {"modo": modo_efetivo(), "gerador": obter_embedder().identificador,
                        "limiar": obter_embedder().limiar, "top_k": config.RECUPERACAO_TOP_K},
        "demandas": [d["id"] for d in demandas],
        "condicoes": condicoes,
        "concluido": not pendentes,
        "pendentes": len(pendentes),
        "interrompido": interrompido,
        "resumo": _resumo_e9(linhas, condicoes),
        "comparacoes": _comparacoes_e9(linhas),
        "linhas": linhas,
    }
