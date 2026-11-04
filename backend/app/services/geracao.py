"""Orquestrador da geracao assistida.

Realiza a classe `Orquestrador` do diagrama de classes (Figura 6) e materializa o
fluxo do diagrama de atividades (Figura 5):

    minimizar -> recuperar -> portao de suficiencia -> montar contexto ->
    gerar -> normalizar -> verificar -> analisar -> persistir versao +
    vinculos -> auditar

Todas as etapas com efeito no banco acontecem na mesma transacao, de modo que uma
minuta nunca existe sem seus vinculos de rastreabilidade nem sem seu registro de
auditoria.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import auditoria, recuperacao, verificacao
from ..core.analise import analisar
from ..core.conteudo import ORIGEM_AGENTE, ORIGEM_MODELO, normalizar_conteudo
from ..core.indice import Ocorrencia
from ..llm import prompt as prompt_mod
from ..llm.base import ErroProvedorLLM, RespostaInvalida
from ..llm.factory import obter_provedor
from ..models import DocumentoDFD, Minuta, MinutaTrecho, TrechoNormativo, Usuario

log = logging.getLogger(__name__)


class FundamentoInsuficiente(RuntimeError):
    """Nenhum trecho superou o limiar de similaridade (fluxo alternativo A1)."""

    def __init__(self, descartados: int, limiar: float, melhor: float | None = None) -> None:
        super().__init__("nenhum fundamento normativo relevante para esta demanda")
        self.descartados = descartados
        self.limiar = limiar
        self.melhor = melhor


@dataclass(slots=True)
class ResultadoGeracao:
    minuta: Minuta
    fontes: list[dict[str, Any]]
    verificacao: dict[str, Any]
    pendencias: list[str]
    analise: dict[str, Any] = field(default_factory=dict)
    desempenho: dict[str, float] = field(default_factory=dict)


class Cronometro:
    """Tempo por etapa do fluxo, em milissegundos (RNF04).

    Gravado nos parametros de cada versao e na auditoria, de modo que o tempo de
    resposta deixa de ser estimativa e passa a ser medido em cada geracao.
    """

    def __init__(self) -> None:
        self.tempos: dict[str, float] = {}
        self._inicio = time.perf_counter()

    @contextmanager
    def etapa(self, nome: str):
        inicio = time.perf_counter()
        try:
            yield
        finally:
            self.tempos[nome] = round((time.perf_counter() - inicio) * 1000, 2)

    def total(self) -> dict[str, float]:
        return {**self.tempos, "total": round((time.perf_counter() - self._inicio) * 1000, 2)}


def contexto_da_versao(minuta: Minuta) -> list[dict[str, Any]]:
    """Trechos vinculados a uma versao, no formato do contexto (T1..Tn)."""
    return [
        {
            "marcador": f"T{v.ordem}",
            "referencia": v.trecho.citacao,
            "texto": v.trecho.texto_indexado,
            "similaridade": float(v.similaridade) if v.similaridade is not None else 0.0,
        }
        for v in minuta.vinculos
    ]


def avaliar(minuta: Minuta, descricao: str) -> ResultadoGeracao:
    """Recalcula verificacao e analise de uma versao ja gravada (leitura)."""
    fontes = contexto_da_versao(minuta)
    relatorio = verificacao.verificar(minuta.conteudo or {}, fontes, descricao).para_dict()
    return ResultadoGeracao(
        minuta=minuta,
        fontes=fontes,
        verificacao=relatorio,
        pendencias=verificacao.pendencias(minuta.conteudo or {}),
        analise=analisar(minuta.conteudo or {}, fontes, descricao, verificacao=relatorio),
    )


class Orquestrador:
    """Coordena a geracao de uma versao de minuta."""

    def __init__(self, sessao: Session) -> None:
        self._sessao = sessao

    # -------------------------------------------------------------- publico
    def gerar(
        self,
        *,
        dfd: DocumentoDFD,
        usuario: Usuario,
        top_k: int | None = None,
        limiar: float | None = None,
        provedor: str | None = None,
        usar_rag: bool = True,
    ) -> ResultadoGeracao:
        """Gera uma nova versao da minuta para o DFD informado."""
        demanda = dfd.demanda
        descricao = demanda.descricao          # ja gravada minimizada (RN06)
        cronometro = Cronometro()

        # 1) recuperacao + portao de suficiencia ------------------------------
        if usar_rag:
            with cronometro.etapa("recuperacao"):
                resultado = recuperacao.recuperar(descricao, k=top_k, limiar=limiar)
            if not resultado.suficiente:
                log.info("DFD %s: geracao barrada pelo portao de suficiencia", dfd.id_dfd)
                melhor = max((t.similaridade for t in resultado.trechos), default=None)
                raise FundamentoInsuficiente(resultado.descartados, resultado.limiar, melhor)
            contexto = resultado.contexto_para_dict()
            bloco = resultado.bloco_contexto()
            trechos = resultado.trechos
            limiar_usado = resultado.limiar
        else:
            # Experimento 1 (sem RAG): o modelo gera sem contexto normativo.
            contexto, bloco, trechos, limiar_usado = [], "(sem contexto normativo)", [], None

        # 2) geracao ----------------------------------------------------------
        provedor_llm = obter_provedor(provedor)
        prompt_usuario = prompt_mod.montar_prompt_usuario(
            unidade=demanda.unidade_requisitante,
            descricao=descricao,
            bloco_contexto=bloco,
            formato=json.dumps(prompt_mod.MODELO_SAIDA, ensure_ascii=False, indent=2),
        )
        try:
            with cronometro.etapa("geracao"):
                resposta = provedor_llm.completar(prompt_mod.PROMPT_SISTEMA, prompt_usuario)
                bruto = resposta.como_json()
        except RespostaInvalida as erro:
            raise ErroProvedorLLM(f"saida do modelo fora do esquema: {erro}") from erro

        # 3) normalizacao, verificacao e analise -----------------------------
        with cronometro.etapa("verificacao"):
            conteudo = normalizar_conteudo(bruto, origem=ORIGEM_MODELO)
            relatorio = verificacao.verificar(conteudo, contexto, descricao)
            pendentes = verificacao.pendencias(conteudo)
            analise = analisar(conteudo, contexto, descricao, verificacao=relatorio.para_dict())

        # 4) persistencia da versao + 5) auditoria ----------------------------
        # Os tempos das etapas 1 a 3 ficam gravados na propria versao; o da
        # persistencia so e conhecido depois e segue apenas na resposta.
        tempos_previos = cronometro.total()
        with cronometro.etapa("persistencia"):
            minuta = self._gravar_versao(
                dfd=dfd,
                usuario=usuario,
                conteudo=conteudo,
                origem="geracao",
                modelo=resposta.modelo,
                parametros={
                    **resposta.parametros,
                    "top_k": top_k,
                    "limiar": limiar_usado,
                    "usar_rag": usar_rag,
                    "trechos_recuperados": len(trechos),
                    "tempos_ms": tempos_previos,
                },
            )
            self._vincular_trechos(minuta, trechos)
            auditoria.registrar(
                self._sessao,
                id_usuario=usuario.id_usuario,
                id_dfd=dfd.id_dfd,
                acao="GERACAO",
                detalhe={
                    "versao": minuta.versao,
                    "modelo": resposta.modelo,
                    "trechos": len(trechos),
                    "ancoragem": relatorio.taxa_ancoragem,
                    "inconsistencia": relatorio.inconsistencia_detectada,
                    "prontidao": analise["prontidao"],
                    "tempo_ms": tempos_previos["total"],
                },
            )

        return ResultadoGeracao(
            minuta=minuta,
            fontes=contexto,
            verificacao=relatorio.para_dict(),
            pendencias=pendentes,
            analise=analise,
            desempenho=cronometro.total(),
        )

    def revisar(
        self,
        *,
        dfd: DocumentoDFD,
        usuario: Usuario,
        conteudo: dict[str, Any],
        correcao_ia: dict[str, str] | None = None,
    ) -> ResultadoGeracao | None:
        """Grava a versao editada pelo agente publico, mantendo o historico.

        Devolve None quando o conteudo nao mudou: salvar sem alteracao nao cria
        versao (e nao invalida uma aprovacao ja registrada).

        Args:
            correcao_ia: provedor e modelo da revisao de redacao aceita pelo
                agente, registrados na versao e na auditoria.
        """
        anterior = dfd.versao_atual
        conteudo = normalizar_conteudo(
            conteudo,
            anterior=anterior.conteudo if anterior else None,
            origem=ORIGEM_AGENTE,
        )
        if anterior is not None and conteudo == normalizar_conteudo(
            anterior.conteudo, origem=ORIGEM_MODELO
        ):
            return None

        contexto = contexto_da_versao(anterior) if anterior else []
        descricao = dfd.demanda.descricao
        relatorio = verificacao.verificar(conteudo, contexto, descricao)

        minuta = self._gravar_versao(
            dfd=dfd,
            usuario=usuario,
            conteudo=conteudo,
            origem="edicao",
            modelo=anterior.modelo_llm if anterior else None,
            parametros={
                "editado_de": anterior.versao if anterior else None,
                **({"correcao_ia": correcao_ia} if correcao_ia else {}),
            },
        )
        if anterior:
            self._copiar_vinculos(anterior, minuta)

        auditoria.registrar(
            self._sessao,
            id_usuario=usuario.id_usuario,
            id_dfd=dfd.id_dfd,
            acao="EDICAO",
            detalhe={
                "versao": minuta.versao,
                "origem_versao": anterior.versao if anterior else None,
                **({"correcao_ia": correcao_ia} if correcao_ia else {}),
            },
        )
        return ResultadoGeracao(
            minuta=minuta,
            fontes=contexto,
            verificacao=relatorio.para_dict(),
            pendencias=verificacao.pendencias(conteudo),
            analise=analisar(conteudo, contexto, descricao, verificacao=relatorio.para_dict()),
        )

    # -------------------------------------------------------------- privados
    def _gravar_versao(
        self,
        *,
        dfd: DocumentoDFD,
        usuario: Usuario,
        conteudo: dict[str, Any],
        origem: str,
        modelo: str | None,
        parametros: dict[str, Any],
    ) -> Minuta:
        proxima = (
            self._sessao.execute(
                select(Minuta.versao)
                .where(Minuta.id_dfd == dfd.id_dfd)
                .order_by(Minuta.versao.desc())
                .limit(1)
            ).scalar_one_or_none()
            or 0
        ) + 1

        minuta = Minuta(
            id_dfd=dfd.id_dfd,
            versao=proxima,
            conteudo=conteudo,
            origem=origem,
            id_usuario=usuario.id_usuario,
            modelo_llm=modelo,
            versao_prompt=prompt_mod.VERSAO,
            parametros=parametros,
        )
        self._sessao.add(minuta)
        # Nova versao ainda nao foi avaliada: uma aprovacao anterior nao vale
        # para ela (RN01), e a situacao do DFD volta a "em elaboracao".
        dfd.situacao = "em_elaboracao"
        self._sessao.flush()
        self._sessao.expire(dfd, ["minutas"])
        log.info("DFD %s: versao %d gravada (%s)", dfd.id_dfd, proxima, origem)
        return minuta

    def _vincular_trechos(self, minuta: Minuta, trechos: list[Ocorrencia]) -> None:
        """Materializa a rastreabilidade (RN02) no modelo de dados."""
        if not trechos:
            return
        referencias = [t.referencia_vetorial for t in trechos]
        encontrados = {
            t.referencia_vetorial: t
            for t in self._sessao.execute(
                select(TrechoNormativo).where(
                    TrechoNormativo.referencia_vetorial.in_(referencias)
                )
            ).scalars()
        }
        for ordem, ocorrencia in enumerate(trechos, start=1):
            registro = encontrados.get(ocorrencia.referencia_vetorial)
            if registro is None:
                log.warning(
                    "Trecho %s esta no indice mas nao no banco; reindexe a base",
                    ocorrencia.referencia_vetorial,
                )
                continue
            self._sessao.add(
                MinutaTrecho(
                    id_minuta=minuta.id_minuta,
                    id_trecho=registro.id_trecho,
                    ordem=ordem,
                    similaridade=round(ocorrencia.similaridade, 4),
                )
            )
        self._sessao.flush()
        self._sessao.expire(minuta, ["vinculos"])

    def _copiar_vinculos(self, origem: Minuta, destino: Minuta) -> None:
        for vinculo in origem.vinculos:
            self._sessao.add(
                MinutaTrecho(
                    id_minuta=destino.id_minuta,
                    id_trecho=vinculo.id_trecho,
                    ordem=vinculo.ordem,
                    similaridade=vinculo.similaridade,
                )
            )
        self._sessao.flush()
        self._sessao.expire(destino, ["vinculos"])


__all__ = [
    "Orquestrador",
    "ResultadoGeracao",
    "FundamentoInsuficiente",
    "avaliar",
    "contexto_da_versao",
]
