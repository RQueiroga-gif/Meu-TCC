"""Contratos de entrada e saida da API (Pydantic v2).

Tipar as fronteiras e o que permite validar toda entrada antes de qualquer efeito
sobre o banco, e o que gera a documentacao OpenAPI usada como evidencia do
contrato consumido pela interface.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ------------------------------------------------------------------ auth
class LoginEntrada(BaseModel):
    """Aceita o e-mail institucional ou um nome de usuario (ex.: 'ronan')."""

    email: str = Field(min_length=3, max_length=120, description="E-mail ou nome de usuário")
    senha: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _identificador(cls, v: str) -> str:
        v = v.strip().lower()
        if " " in v:
            raise ValueError("o login não pode conter espaços")
        return v


class LoginSaida(BaseModel):
    token: str
    perfil: str
    nome: str
    unidade: str | None = None
    expira_em_minutos: int


class UsuarioSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: int
    nome: str
    email: str
    perfil: str
    unidade: str | None = None


# ------------------------------------------------------------------ normas
class NormaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_norma: int
    sigla: str
    tipo: str
    numero: str
    ano: int
    ementa: str
    vigente: bool
    url_fonte: str | None = None
    hash_fonte: str | None = None
    data_indexacao: datetime | None = None


class IndexacaoSaida(BaseModel):
    sigla: str
    dispositivos: int
    indexados: int
    hash_fonte: str
    alterada: bool


# ------------------------------------------------------------------ demandas
class DemandaEntrada(BaseModel):
    unidade_requisitante: str = Field(min_length=2, max_length=120)
    titulo: str = Field(min_length=5, max_length=255)
    descricao: str = Field(min_length=20, max_length=20_000)

    @field_validator("titulo", "descricao", "unidade_requisitante")
    @classmethod
    def _sem_espacos_sobrando(cls, v: str) -> str:
        return " ".join(v.split())


class DemandaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_demanda: int
    id_dfd: int
    unidade_requisitante: str
    titulo: str
    descricao: str
    situacao: str
    data_registro: datetime
    dados_pessoais_removidos: dict[str, int] = Field(default_factory=dict)


class DFDResumo(BaseModel):
    id_dfd: int
    numero: str | None
    titulo: str
    unidade_requisitante: str
    responsavel: str | None = None
    situacao: str
    versao_atual: int | None
    data_criacao: datetime


# ------------------------------------------------------------------ geracao
class GeracaoEntrada(BaseModel):
    """Parametros opcionais; os padroes vem da configuracao.

    `provedor` e `usar_rag=False` existem para os experimentos do TCC e sao
    restritos ao perfil gestor: um demandante nunca gera minuta sem RAG.
    """

    top_k: int | None = Field(default=None, ge=1, le=20)
    limiar: float | None = Field(default=None, ge=0.0, le=1.0)
    provedor: str | None = Field(
        default=None,
        pattern="^(gemini|groq|echo)$",
        description="gemini | groq | echo",
    )
    usar_rag: bool = Field(default=True, description="False apenas no Experimento 1")


class TrechoSaida(BaseModel):
    marcador: str
    referencia: str
    texto: str
    similaridade: float


class MinutaSaida(BaseModel):
    id_minuta: int
    id_dfd: int
    numero: str | None = None
    titulo: str | None = None
    unidade_requisitante: str | None = None
    responsavel: str | None = None
    situacao: str | None = None
    aprovada: bool = Field(default=False, description="A versao atual tem aprovacao registrada")
    versao: int
    conteudo: dict[str, Any]
    origem: str
    modelo_llm: str | None
    versao_prompt: str | None
    data_geracao: datetime
    fontes: list[TrechoSaida] = Field(default_factory=list)
    verificacao: dict[str, Any] = Field(default_factory=dict)
    pendencias: list[str] = Field(default_factory=list)
    analise: dict[str, Any] = Field(default_factory=dict)
    desempenho: dict[str, float] = Field(
        default_factory=dict, description="Tempo por etapa da geracao, em ms (RNF04)"
    )
    versao_criada: bool = Field(
        default=True, description="False quando a revisao nao alterou o conteudo"
    )


class CorrecaoAplicada(BaseModel):
    """Origem da sugestao aceita pelo agente, gravada nos parametros da versao."""

    provedor: str = Field(max_length=20)
    modelo: str = Field(max_length=120)


class RevisaoEntrada(BaseModel):
    conteudo: dict[str, Any] = Field(description="Minuta editada pelo agente público")
    correcao_ia: CorrecaoAplicada | None = Field(
        default=None, description="Preenchido quando o agente aceita a revisão de redação"
    )


# ------------------------------------------------------------------ correcao
class CorrecaoEntrada(BaseModel):
    conteudo: dict[str, Any] | None = Field(
        default=None, description="Texto em edição; sem ele, revisa a versão atual"
    )


class CampoCorrigidoSaida(BaseModel):
    campo: str
    original: str
    sugerido: str
    alterado: bool
    motivo_descarte: str | None = None


class CorrecaoSaida(BaseModel):
    """Sugestao de redacao: nada e gravado ate o agente aceitar (UC05)."""

    provedor: str
    modelo: str
    campos: list[CampoCorrigidoSaida]
    observacoes: list[str] = Field(default_factory=list)
    conteudo_sugerido: dict[str, Any]


# ------------------------------------------------------------------ validacao
CAMPOS_ART_8 = (
    "justificativa_necessidade",
    "descricao_objeto",
    "quantidade",
    "estimativa_valor",
    "data_pretendida",
    "grau_prioridade",
    "vinculacao_outro_dfd",
    "area_requisitante",
)


class ValidacaoEntrada(BaseModel):
    id_minuta: int
    decisao: str = Field(pattern="^(aprovado|devolvido)$")
    justificativa: str | None = Field(default=None, max_length=5_000)
    campos_confirmados: list[str] = Field(
        default_factory=list, description="Campos do art. 8º confirmados individualmente (RF07)"
    )

    @field_validator("campos_confirmados")
    @classmethod
    def _campos_conhecidos(cls, v: list[str]) -> list[str]:
        desconhecidos = sorted(set(v) - set(CAMPOS_ART_8))
        if desconhecidos:
            raise ValueError(f"campos desconhecidos: {', '.join(desconhecidos)}")
        return sorted(set(v), key=CAMPOS_ART_8.index)


class ValidacaoSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_validacao: int
    id_dfd: int
    id_minuta: int
    decisao: str
    responsavel: str
    data_validacao: datetime


class ExportacaoSaida(BaseModel):
    id_exportacao: int
    id_dfd: int
    id_minuta: int
    formato: str
    hash_arquivo: str
    arquivo: str
    url_download: str
    data_hora: datetime


# ------------------------------------------------------------------ auditoria
class AuditoriaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_registro: int
    id_usuario: int
    usuario_nome: str | None = None
    id_dfd: int | None
    acao: str
    detalhe: dict[str, Any] | None
    data_hora: datetime
    hash_registro: str


class IntegridadeSaida(BaseModel):
    integra: bool
    total: int
    primeiro_divergente: int | None


# ------------------------------------------------------------------ erros
class Erro(BaseModel):
    detail: str


__all__ = [name for name in dir() if name[0].isupper()]
