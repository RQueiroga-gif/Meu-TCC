"""Geracao do DFD aprovado em .docx (RF09).

O documento sai no formato editavel usado no processo eletronico e carrega, no
proprio arquivo, a evidencia de rastreabilidade e de supervisao:

  * identificacao do DFD e da demanda;
  * os oito campos do art. 8º do Decreto nº 10.947/2022, com a fundamentacao de
    cada campo redigido resolvida em citacoes (e nao em marcadores T1, T2...) e a
    origem de cada valor extraido;
  * o registro da validacao humana (RN01, RN04);
  * Anexo I — texto integral dos dispositivos que fundamentaram a minuta (RN02);
  * Anexo II — relatorio de verificacao e de conformidade com o art. 8º;
  * Anexo III — historico de versoes (nada e sobrescrito).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ..config import config
from ..core.verificacao import CAMPOS_REDIGIDOS
from ..llm.prompt import CAMPOS_ART_8
from ..models import DocumentoDFD, Minuta, Validacao
from .geracao import avaliar

log = logging.getLogger(__name__)

SITUACAO_ROTULO = {"ok": "Conforme", "alerta": "Atenção", "pendente": "Pendente"}
CINZA = RGBColor(0x59, 0x59, 0x59)


# ------------------------------------------------------------------ utilidades
def _fuso():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(config.FUSO_HORARIO)
    except Exception:  # noqa: BLE001 — sem base de fusos (Windows sem tzdata)
        log.warning("Fuso %s indisponível; datas em UTC", config.FUSO_HORARIO)
        return timezone.utc


def _local(momento: datetime | None) -> str:
    """Converte o instante gravado em UTC para o fuso de exibição."""
    if momento is None:
        return "—"
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(_fuso()).strftime("%d/%m/%Y às %H:%M")


def _valor(campo: Any) -> str:
    """Extrai o texto de um campo da minuta, qualquer que seja seu formato."""
    if isinstance(campo, dict):
        return str(campo.get("texto") or campo.get("valor") or "").strip()
    return str(campo or "").strip()


def _paragrafo(doc, texto: str, *, tamanho: float = 11, negrito: bool = False,
               italico: bool = False, cor: RGBColor | None = None,
               alinhamento=WD_ALIGN_PARAGRAPH.JUSTIFY, espaco_depois: float = 6):
    p = doc.add_paragraph()
    p.alignment = alinhamento
    p.paragraph_format.space_after = Pt(espaco_depois)
    execucao = p.add_run(texto)
    execucao.bold = negrito
    execucao.italic = italico
    execucao.font.size = Pt(tamanho)
    if cor is not None:
        execucao.font.color.rgb = cor
    return p


def _titulo(doc, texto: str, nivel: int = 2) -> None:
    cabecalho = doc.add_heading(texto, level=nivel)
    cabecalho.paragraph_format.space_before = Pt(12 if nivel <= 2 else 8)
    cabecalho.paragraph_format.keep_with_next = True


def _sombrear(celula, cor_hex: str) -> None:
    propriedades = celula._tc.get_or_add_tcPr()
    sombra = OxmlElement("w:shd")
    sombra.set(qn("w:val"), "clear")
    sombra.set(qn("w:color"), "auto")
    sombra.set(qn("w:fill"), cor_hex)
    propriedades.append(sombra)


def _tabela(doc, cabecalho: list[str] | None, linhas: list[list[str]],
            larguras: list[float] | None = None, tamanho: float = 9.5):
    total_linhas = len(linhas) + (1 if cabecalho else 0)
    colunas = len(cabecalho or linhas[0])
    tabela = doc.add_table(rows=total_linhas, cols=colunas)
    tabela.style = "Table Grid"
    tabela.alignment = WD_TABLE_ALIGNMENT.CENTER

    def escrever(celula, texto: str, negrito: bool = False) -> None:
        celula.text = ""
        execucao = celula.paragraphs[0].add_run(texto)
        execucao.font.size = Pt(tamanho)
        execucao.bold = negrito

    inicio = 0
    if cabecalho:
        for j, texto in enumerate(cabecalho):
            escrever(tabela.rows[0].cells[j], texto, negrito=True)
            _sombrear(tabela.rows[0].cells[j], "D9E2F3")
        inicio = 1
    for i, linha in enumerate(linhas, start=inicio):
        for j, texto in enumerate(linha):
            escrever(tabela.rows[i].cells[j], texto, negrito=(not cabecalho and j == 0))
            if not cabecalho and j == 0:
                _sombrear(tabela.rows[i].cells[j], "F2F2F2")
    if larguras:
        for linha in tabela.rows:
            for j, largura in enumerate(larguras):
                linha.cells[j].width = Cm(largura)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return tabela


def _campo_pagina(paragrafo, instrucao: str) -> None:
    """Insere um campo do Word (PAGE, NUMPAGES) no paragrafo."""
    execucao = paragrafo.add_run()
    inicio = OxmlElement("w:fldChar")
    inicio.set(qn("w:fldCharType"), "begin")
    codigo = OxmlElement("w:instrText")
    codigo.set(qn("xml:space"), "preserve")
    codigo.text = instrucao
    fim = OxmlElement("w:fldChar")
    fim.set(qn("w:fldCharType"), "end")
    execucao._r.append(inicio)
    execucao._r.append(codigo)
    execucao._r.append(fim)
    execucao.font.size = Pt(8)


def _configurar(doc, rotulo_rodape: str) -> None:
    """Pagina A4, margens da norma de redacao oficial, fonte e rodape paginado."""
    secao = doc.sections[0]
    secao.page_height, secao.page_width = Cm(29.7), Cm(21.0)
    secao.top_margin, secao.left_margin = Cm(3), Cm(3)
    secao.bottom_margin, secao.right_margin = Cm(2), Cm(2)

    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")

    rodape = secao.footer.paragraphs[0]
    rodape.alignment = WD_ALIGN_PARAGRAPH.CENTER
    texto = rodape.add_run(f"{rotulo_rodape} — página ")
    texto.font.size = Pt(8)
    _campo_pagina(rodape, "PAGE")
    separador = rodape.add_run(" de ")
    separador.font.size = Pt(8)
    _campo_pagina(rodape, "NUMPAGES")


def _aprovacao(dfd: DocumentoDFD, minuta: Minuta) -> Validacao | None:
    """Ultima decisao sobre a versao exportada, se for de aprovacao."""
    decisoes = sorted(
        (v for v in (dfd.validacoes or []) if v.id_minuta == minuta.id_minuta),
        key=lambda v: v.id_validacao,
    )
    return decisoes[-1] if decisoes and decisoes[-1].decisao == "aprovado" else None


# ------------------------------------------------------------------ documento
def gerar_docx(
    *,
    dfd: DocumentoDFD,
    minuta: Minuta,
    destino: str | Path,
    emitido_por: str | None = None,
) -> Path:
    """Escreve o DFD aprovado em .docx e devolve o caminho do arquivo."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    demanda = dfd.demanda
    conteudo: dict[str, Any] = minuta.conteudo or {}
    avaliacao = avaliar(minuta, demanda.descricao)
    analise = avaliacao.analise
    citacoes = {f["marcador"]: f["referencia"] for f in avaliacao.fontes}
    numero = dfd.numero or f"DFD {dfd.id_dfd:04d}"
    aprovacao = _aprovacao(dfd, minuta)

    doc = Document()
    _configurar(doc, f"{numero} — versão {minuta.versao}")

    # ------------------------------------------------ cabecalho
    titulo = doc.add_heading("DOCUMENTO DE FORMALIZAÇÃO DA DEMANDA", level=1)
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _paragrafo(
        doc,
        "Art. 12, VII, da Lei nº 14.133/2021 e art. 8º do Decreto nº 10.947/2022",
        tamanho=9, italico=True, cor=CINZA, alinhamento=WD_ALIGN_PARAGRAPH.CENTER,
    )

    _titulo(doc, "Identificação")
    _tabela(
        doc,
        None,
        [
            ["Número", numero],
            ["Exercício", str(dfd.exercicio or "—")],
            ["Objeto (título)", demanda.titulo],
            ["Unidade requisitante", demanda.unidade_requisitante],
            ["Responsável pela demanda", demanda.usuario.nome],
            ["Versão do documento", f"{minuta.versao} ({'gerada' if minuta.origem == 'geracao' else 'revisada pelo agente'})"],
            ["Data de emissão", _local(datetime.now(timezone.utc))],
        ],
        larguras=[5.0, 11.0],
    )

    # ------------------------------------------------ campos do art. 8
    _titulo(doc, "Conteúdo do documento (art. 8º do Decreto nº 10.947/2022)")
    for chave, rotulo in CAMPOS_ART_8.items():
        _titulo(doc, rotulo[0].upper() + rotulo[1:], nivel=3)
        campo = conteudo.get(chave) if isinstance(conteudo.get(chave), dict) else {}

        if chave == "area_requisitante":
            _paragrafo(doc, f"{demanda.unidade_requisitante} — responsável: {demanda.usuario.nome}.")
            continue

        texto = _valor(campo)
        _paragrafo(doc, texto or "Não informado.", italico=not texto)

        if chave in CAMPOS_REDIGIDOS and campo.get("fundamentos"):
            referencias = [citacoes.get(m, m) for m in campo["fundamentos"]]
            _paragrafo(doc, "Fundamentação: " + "; ".join(referencias) + ".",
                       tamanho=9, cor=CINZA)
        elif chave not in CAMPOS_REDIGIDOS and texto:
            origem = (
                "informação declarada pelo agente público na revisão"
                if campo.get("origem") == "agente"
                else "informação extraída da demanda registrada"
            )
            _paragrafo(doc, f"Origem: {origem}.", tamanho=9, cor=CINZA)

    # ------------------------------------------------ validacao humana (RN01)
    _titulo(doc, "Validação")
    if aprovacao is not None:
        linhas = [
            ["Decisão", "Aprovado"],
            ["Responsável", aprovacao.usuario.nome],
            ["Data", _local(aprovacao.data_validacao)],
            ["Versão aprovada", str(minuta.versao)],
            ["Confirmação", "Todos os campos do art. 8º confirmados individualmente."],
        ]
        if aprovacao.justificativa:
            linhas.append(["Observações", aprovacao.justificativa])
        _tabela(doc, None, linhas, larguras=[5.0, 11.0])
        _paragrafo(
            doc,
            f"Declaro que revisei a minuta e os fundamentos normativos recuperados, e "
            f"aprovo o documento sob minha responsabilidade. — {aprovacao.usuario.nome}",
            italico=True,
        )
    else:
        _paragrafo(
            doc,
            "Documento sujeito à validação do agente público responsável.",
            italico=True,
        )

    _paragrafo(
        doc,
        "Minuta produzida com apoio de recuperação normativa e modelo de linguagem "
        f"({minuta.modelo_llm or 'não informado'}, prompt {minuta.versao_prompt or '—'}); "
        "a decisão administrativa permanece integralmente com o agente público."
        + (f" Emitido por {emitido_por}." if emitido_por else ""),
        tamanho=8, cor=CINZA,
    )

    # ------------------------------------------------ Anexo I — fundamentos
    vinculos = list(minuta.vinculos)
    if vinculos:
        doc.add_page_break()
        _titulo(doc, "Anexo I — Fundamentos normativos recuperados", nivel=1)
        _paragrafo(
            doc,
            "Dispositivos vigentes recuperados da base normativa e vinculados a esta "
            "versão. A similaridade indica a proximidade semântica com a demanda.",
            tamanho=9, cor=CINZA,
        )
        citados = {
            m for c in CAMPOS_REDIGIDOS for m in (conteudo.get(c) or {}).get("fundamentos", [])
        }
        _tabela(
            doc,
            ["Ref.", "Dispositivo", "Similaridade", "Citado"],
            [
                [
                    f"T{v.ordem}",
                    v.trecho.citacao,
                    f"{float(v.similaridade):.2f}" if v.similaridade is not None else "—",
                    "Sim" if f"T{v.ordem}" in citados else "Não",
                ]
                for v in vinculos
            ],
            larguras=[1.3, 9.7, 2.5, 2.5],
        )
        for vinculo in vinculos:
            _paragrafo(doc, f"[T{vinculo.ordem}] {vinculo.trecho.citacao}",
                       negrito=True, tamanho=10, espaco_depois=2)
            _paragrafo(doc, vinculo.trecho.texto, tamanho=10)

    # ------------------------------------------------ Anexo II — conformidade
    doc.add_page_break()
    _titulo(doc, "Anexo II — Relatório de verificação e conformidade", nivel=1)
    indicadores = analise.get("indicadores", {})
    taxa = indicadores.get("taxa_ancoragem")
    media = indicadores.get("similaridade_media")
    _tabela(
        doc,
        None,
        [
            ["Taxa de ancoragem", f"{taxa:.0%}" if taxa is not None else "—"],
            ["Citações inválidas", str(indicadores.get("citacoes_invalidas", 0))],
            ["Fontes recuperadas / citadas",
             f"{indicadores.get('fontes_recuperadas', 0)} / {indicadores.get('fontes_citadas', 0)}"],
            ["Normas citadas", ", ".join(indicadores.get("normas_citadas") or []) or "—"],
            ["Similaridade média", f"{media:.2f}" if media is not None else "—"],
            ["Índice de prontidão", f"{analise.get('prontidao', 0):.0%}"],
        ],
        larguras=[6.0, 10.0],
    )
    _tabela(
        doc,
        ["Campo do art. 8º", "Situação", "Observação"],
        [
            [
                CAMPOS_ART_8.get(item["campo"], item["campo"]),
                SITUACAO_ROTULO.get(item["situacao"], item["situacao"]),
                item["mensagem"],
            ]
            for item in analise.get("checklist", [])
        ],
        larguras=[6.0, 2.5, 7.5],
    )
    _paragrafo(
        doc,
        "Verificação automática e determinística: confere se cada fundamento citado "
        "existe entre os trechos recuperados, se nenhum artigo mencionado está fora "
        "deles e se cada valor extraído aparece literalmente na demanda.",
        tamanho=8, cor=CINZA,
    )

    # ------------------------------------------------ Anexo III — versoes
    _titulo(doc, "Anexo III — Histórico de versões", nivel=1)
    decisoes = {v.id_minuta: v.decisao for v in sorted(dfd.validacoes or [], key=lambda v: v.id_validacao)}
    _tabela(
        doc,
        ["Versão", "Origem", "Data", "Modelo", "Decisão"],
        [
            [
                str(m.versao),
                "geração" if m.origem == "geracao" else "edição humana",
                _local(m.data_geracao),
                m.modelo_llm or "—",
                {"aprovado": "aprovada", "devolvido": "devolvida"}.get(decisoes.get(m.id_minuta), "—"),
            ]
            for m in dfd.minutas
        ],
        larguras=[1.6, 3.2, 3.8, 4.4, 3.0],
    )

    doc.save(destino)
    log.info("DFD exportado: %s", destino)
    return destino


__all__ = ["gerar_docx"]
