"""Modelo de objetos — realiza o Quadro 10 (modelo logico) do TCC.

Cada classe corresponde a uma entidade do diagrama de classes (Figura 6) e a uma
tabela do modelo logico. As regras invariantes (RN01, RN05) nao estao aqui: elas
vivem em gatilhos do banco, de modo que nenhum caminho de aplicacao as contorne.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def agora() -> datetime:
    """Instante atual em UTC, sem microssegundos (comparavel entre dialetos)."""
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


class Base(DeclarativeBase):
    """Base declarativa do SQLAlchemy."""


class Usuario(Base):
    __tablename__ = "usuario"

    id_usuario: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    perfil: Mapped[str] = mapped_column(String(20), nullable=False)
    senha_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    unidade: Mapped[str | None] = mapped_column(String(120))
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Usuario {self.email} ({self.perfil})>"


class Norma(Base):
    __tablename__ = "norma"

    id_norma: Mapped[int] = mapped_column(Integer, primary_key=True)
    sigla: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    numero: Mapped[str] = mapped_column(String(20), nullable=False)
    ano: Mapped[int] = mapped_column(Integer, nullable=False)
    ementa: Mapped[str] = mapped_column(Text, nullable=False)
    orgao: Mapped[str | None] = mapped_column(String(120))
    data_publicacao: Mapped[Any | None] = mapped_column(Date)
    vigente: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    url_fonte: Mapped[str | None] = mapped_column(String(500))
    hash_fonte: Mapped[str | None] = mapped_column(String(64))
    data_indexacao: Mapped[datetime | None] = mapped_column(DateTime)

    trechos: Mapped[list["TrechoNormativo"]] = relationship(
        back_populates="norma", cascade="all, delete-orphan"
    )

    @property
    def referencia(self) -> str:
        """Citacao no padrao juridico: 'Lei no 14.133/2021'."""
        return f"{self.tipo} nº {self.numero}/{self.ano}"


class TrechoNormativo(Base):
    __tablename__ = "trecho_normativo"

    id_trecho: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_norma: Mapped[int] = mapped_column(
        ForeignKey("norma.id_norma", ondelete="CASCADE"), nullable=False
    )
    dispositivo: Mapped[str] = mapped_column(String(120), nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    texto_indexado: Mapped[str] = mapped_column(Text, nullable=False)
    artigo: Mapped[str | None] = mapped_column(String(20))
    referencia_vetorial: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True
    )
    vigente: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    norma: Mapped[Norma] = relationship(back_populates="trechos")

    __table_args__ = (Index("ix_trecho_norma", "id_norma"),)

    @property
    def citacao(self) -> str:
        return f"{self.norma.referencia}, {self.dispositivo}"


class Demanda(Base):
    __tablename__ = "demanda"

    id_demanda: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_usuario: Mapped[int] = mapped_column(ForeignKey("usuario.id_usuario"), nullable=False)
    unidade_requisitante: Mapped[str] = mapped_column(String(120), nullable=False)
    titulo: Mapped[str] = mapped_column(String(255), nullable=False)
    descricao: Mapped[str] = mapped_column(Text, nullable=False)
    data_registro: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=agora)
    situacao: Mapped[str] = mapped_column(String(20), nullable=False, default="rascunho")

    usuario: Mapped[Usuario] = relationship()
    dfd: Mapped["DocumentoDFD | None"] = relationship(
        back_populates="demanda", uselist=False, cascade="all, delete-orphan"
    )


class DocumentoDFD(Base):
    __tablename__ = "documento_dfd"

    id_dfd: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_demanda: Mapped[int] = mapped_column(
        ForeignKey("demanda.id_demanda", ondelete="CASCADE"), nullable=False, unique=True
    )
    numero: Mapped[str | None] = mapped_column(String(30))
    exercicio: Mapped[int | None] = mapped_column(Integer)
    situacao: Mapped[str] = mapped_column(String(20), nullable=False, default="em_elaboracao")
    data_criacao: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=agora)

    demanda: Mapped[Demanda] = relationship(back_populates="dfd")
    minutas: Mapped[list["Minuta"]] = relationship(
        back_populates="dfd", cascade="all, delete-orphan", order_by="Minuta.versao"
    )
    validacoes: Mapped[list["Validacao"]] = relationship(
        back_populates="dfd", cascade="all, delete-orphan", order_by="Validacao.data_validacao"
    )

    @property
    def versao_atual(self) -> "Minuta | None":
        return self.minutas[-1] if self.minutas else None


class Minuta(Base):
    __tablename__ = "minuta"

    id_minuta: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_dfd: Mapped[int] = mapped_column(
        ForeignKey("documento_dfd.id_dfd", ondelete="CASCADE"), nullable=False
    )
    versao: Mapped[int] = mapped_column(Integer, nullable=False)
    conteudo: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    origem: Mapped[str] = mapped_column(String(10), nullable=False)
    id_usuario: Mapped[int] = mapped_column(ForeignKey("usuario.id_usuario"), nullable=False)
    modelo_llm: Mapped[str | None] = mapped_column(String(120))
    versao_prompt: Mapped[str | None] = mapped_column(String(20))
    parametros: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    data_geracao: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=agora)

    dfd: Mapped[DocumentoDFD] = relationship(back_populates="minutas")
    vinculos: Mapped[list["MinutaTrecho"]] = relationship(
        back_populates="minuta", cascade="all, delete-orphan", order_by="MinutaTrecho.ordem"
    )

    __table_args__ = (UniqueConstraint("id_dfd", "versao", name="uq_minuta_versao"),)


class MinutaTrecho(Base):
    """Rastreabilidade (RN02): liga cada minuta aos trechos que a fundamentam."""

    __tablename__ = "minuta_trecho"

    id_minuta: Mapped[int] = mapped_column(
        ForeignKey("minuta.id_minuta", ondelete="CASCADE"), primary_key=True
    )
    id_trecho: Mapped[int] = mapped_column(
        ForeignKey("trecho_normativo.id_trecho"), primary_key=True
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    similaridade: Mapped[float | None] = mapped_column(Numeric(6, 4))

    minuta: Mapped[Minuta] = relationship(back_populates="vinculos")
    trecho: Mapped[TrechoNormativo] = relationship()


class Validacao(Base):
    """Decisao humana (RN01/RN04), sempre vinculada a versao exata aprovada."""

    __tablename__ = "validacao"

    id_validacao: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_dfd: Mapped[int] = mapped_column(
        ForeignKey("documento_dfd.id_dfd", ondelete="CASCADE"), nullable=False
    )
    id_minuta: Mapped[int] = mapped_column(ForeignKey("minuta.id_minuta"), nullable=False)
    id_usuario: Mapped[int] = mapped_column(ForeignKey("usuario.id_usuario"), nullable=False)
    decisao: Mapped[str] = mapped_column(String(12), nullable=False)
    justificativa: Mapped[str | None] = mapped_column(Text)
    data_validacao: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=agora)

    usuario: Mapped[Usuario] = relationship()
    dfd: Mapped[DocumentoDFD] = relationship(back_populates="validacoes")


class Exportacao(Base):
    __tablename__ = "exportacao"

    id_exportacao: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_dfd: Mapped[int] = mapped_column(
        ForeignKey("documento_dfd.id_dfd", ondelete="CASCADE"), nullable=False
    )
    id_minuta: Mapped[int] = mapped_column(ForeignKey("minuta.id_minuta"), nullable=False)
    id_usuario: Mapped[int] = mapped_column(ForeignKey("usuario.id_usuario"), nullable=False)
    formato: Mapped[str] = mapped_column(String(10), nullable=False, default="docx")
    hash_arquivo: Mapped[str | None] = mapped_column(String(64))
    data_hora: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=agora)


class RegistroAuditoria(Base):
    """Auditoria somente-inclusao, encadeada por hash (RN05, RNF07)."""

    __tablename__ = "registro_auditoria"

    id_registro: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_usuario: Mapped[int] = mapped_column(ForeignKey("usuario.id_usuario"), nullable=False)
    id_dfd: Mapped[int | None] = mapped_column(ForeignKey("documento_dfd.id_dfd"))
    acao: Mapped[str] = mapped_column(String(60), nullable=False)
    detalhe: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    data_hora: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=agora)
    hash_anterior: Mapped[str | None] = mapped_column(String(64))
    hash_registro: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("ix_auditoria_dfd", "id_dfd"),)


__all__ = [
    "Base",
    "agora",
    "Usuario",
    "Norma",
    "TrechoNormativo",
    "Demanda",
    "DocumentoDFD",
    "Minuta",
    "MinutaTrecho",
    "Validacao",
    "Exportacao",
    "RegistroAuditoria",
]
