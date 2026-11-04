"""Configuração da aplicação, lida do ambiente.

Centraliza todo parâmetro que muda entre máquinas ou entre execuções, de modo que
nenhum valor sensível ou dependente de ambiente fique no código (LGPD, art. 46).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parent.parent          # backend/
RAIZ_PROJETO = RAIZ.parent                             # dfd-assistido/
SECRET_KEY_PADRAO = "troque-esta-chave-em-producao"
PROVEDORES = ("anthropic", "gemini", "groq", "ollama", "echo")


class Config(BaseSettings):
    """Parâmetros da aplicação."""

    model_config = SettingsConfigDict(
        env_file=(RAIZ_PROJETO / ".env", RAIZ / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------ aplicação
    APP_NOME: str = "Assistente de DFD"
    APP_VERSAO: str = "0.3.0"
    DEBUG: bool = False

    # ------------------------------------------------------------ banco
    # SQLite (padrão de desenvolvimento) ou MySQL 8 (quando houver mais de um
    # processo escrevendo, por exemplo com o orquestrador n8n).
    DB_DIALETO: str = Field(default="sqlite", description="sqlite | mysql")
    DB_ARQUIVO: str = Field(default=str(RAIZ / "db" / "dfd.sqlite3"))
    DB_HOST: str = "localhost"
    DB_PORTA: int = 3306
    DB_NOME: str = "dfd"
    DB_USUARIO: str = "dfd"
    DB_SENHA: str = ""

    # ------------------------------------------------------------ segurança
    SECRET_KEY: str = Field(default=SECRET_KEY_PADRAO, min_length=8)
    TOKEN_EXPIRA_MINUTOS: int = 480
    BCRYPT_RODADAS: int = 12
    # Tentativas de login com senha errada antes do bloqueio temporario.
    LOGIN_MAX_TENTATIVAS: int = 5
    LOGIN_BLOQUEIO_MINUTOS: int = 15

    # ------------------------------------------------------------ apresentação
    # Datas sao gravadas em UTC; este fuso e usado apenas para exibir (docx).
    FUSO_HORARIO: str = "America/Sao_Paulo"
    PASTA_SAIDA: str = Field(default=str(RAIZ / "saida"))

    # ------------------------------------------------------------ recuperação
    # "BAAI/bge-m3" (referencia), "spacy:pt_core_news_md" (semantico leve, offline)
    # ou "hash" (deterministico, sem qualidade semantica; apenas testes).
    EMBEDDING_MODELO: str = "BAAI/bge-m3"
    INDICE_BACKEND: str = Field(default="numpy", description="numpy | chroma")
    INDICE_ARQUIVO: str = Field(default=str(RAIZ / "db" / "indice.npz"))
    CHROMA_HOST: str = "localhost"
    CHROMA_PORTA: int = 8000
    INDICE_LEXICAL_ARQUIVO: str = Field(default=str(RAIZ / "db" / "indice_lexical.json.gz"))
    # denso | lexical | hibrido | automatico (hibrido com BGE-M3; lexical sem ele).
    # Ver app/core/lexical.py e o capitulo de resultados (experimento E1).
    RECUPERACAO_MODO: str = "automatico"
    RECUPERACAO_TOP_K: int = 6
    # Limiar do BGE-M3, calibrado no E3 (Youden, validacao cruzada em 2 partes):
    # 0,52 barra 2 de 32 demandas legitimas e 1 de 10 pedidos fora do dominio;
    # o valor provisorio anterior (0,45) deixava passar 4 de 10.
    RECUPERACAO_LIMIAR: float = Field(default=0.52, ge=0.0, le=1.0)
    # Limiar usado quando o gerador ativo e o deterministico (hash de n-gramas):
    # suas similaridades ficam numa escala bem mais baixa que a do BGE-M3, e o
    # limiar calibrado para o modelo real barraria toda geracao.
    # Calibrado com o corpus oficial integral (E3, validacao cruzada em 2 partes).
    # O valor anterior (0,10), ajustado sobre as amostras, deixava passar todas as
    # demandas fora do dominio.
    RECUPERACAO_LIMIAR_HASH: float = Field(default=0.46, ge=0.0, le=1.0)
    # Limiar para a media de vetores do spaCy (pt_core_news_md), calibrado na
    # avaliacao de 26/10/2026 (validacao cruzada em 2 partes; ver avaliacao/).
    RECUPERACAO_LIMIAR_SPACY: float = Field(default=0.64, ge=0.0, le=1.0)

    # ------------------------------------------------------------ dados pessoais
    # Reconhecimento de nomes (spaCy) na minimizacao (RN06). Sem a biblioteca ou o
    # modelo, a minimizacao segue apenas com os identificadores de formato definido.
    ANONIMIZADOR_NER: bool = True
    ANONIMIZADOR_MODELO_NER: str = "pt_core_news_md"

    # ------------------------------------------------------------ LLM
    LLM_PROVEDOR: str = Field(
        default="echo", description="anthropic | gemini | groq | ollama | echo"
    )
    # Provedor da revisao de redacao (botao "Revisar texto"); vazio = LLM_PROVEDOR.
    # Usar um fornecedor diferente do gerador evita que o modelo revise a si mesmo.
    LLM_PROVEDOR_CORRECAO: str = ""
    # Modelo da revisao; vazio = o modelo padrao do provedor (ex.: GEMINI_MODELO).
    LLM_MODELO_CORRECAO: str = ""
    LLM_MODELO: str = "claude-haiku-4-5-20251001"
    LLM_TEMPERATURA: float = 0.0
    LLM_MAX_TOKENS: int = 8192
    LLM_TIMEOUT_S: int = 120
    LLM_TENTATIVAS: int = 3
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_MODELO: str = "gemini-3.6-flash"
    # Gemini 3: minimal (nem todos aceitam) | low | medium | high; vazio = padrao do modelo
    GEMINI_NIVEL_RACIOCINIO: str = "low"
    # Groq: modelos abertos (gpt-oss, Qwen, Llama) pela API compativel com a da OpenAI
    GROQ_API_KEY: str = ""
    GROQ_MODELO: str = "openai/gpt-oss-120b"
    GROQ_URL: str = "https://api.groq.com/openai/v1"
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODELO: str = "llama3.1:8b"

    @field_validator("DB_DIALETO")
    @classmethod
    def _dialeto_valido(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"sqlite", "mysql"}:
            raise ValueError("DB_DIALETO deve ser 'sqlite' ou 'mysql'")
        return v

    @field_validator("INDICE_BACKEND")
    @classmethod
    def _indice_valido(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"numpy", "chroma"}:
            raise ValueError("INDICE_BACKEND deve ser 'numpy' ou 'chroma'")
        return v

    @field_validator("RECUPERACAO_MODO")
    @classmethod
    def _modo_valido(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"denso", "lexical", "hibrido", "automatico"}:
            raise ValueError("RECUPERACAO_MODO deve ser denso, lexical, hibrido ou automatico")
        return v

    @field_validator("LLM_PROVEDOR", "LLM_PROVEDOR_CORRECAO")
    @classmethod
    def _provedor_valido(cls, v: str, info: ValidationInfo) -> str:
        v = v.lower().strip()
        if info.field_name == "LLM_PROVEDOR_CORRECAO" and not v:
            return v
        if v not in PROVEDORES:
            raise ValueError(
                f"{info.field_name} deve ser 'anthropic', 'gemini', 'groq', 'ollama' ou 'echo'"
            )
        return v

    @property
    def chave_insegura(self) -> bool:
        """SECRET_KEY padrao ou de exemplo: qualquer um forjaria tokens."""
        return self.SECRET_KEY in {SECRET_KEY_PADRAO, "gere-com-openssl-rand-hex-32"}

    @property
    def url_banco(self) -> str:
        """URL de conexão do SQLAlchemy conforme o dialeto configurado."""
        if self.DB_DIALETO == "sqlite":
            Path(self.DB_ARQUIVO).parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{self.DB_ARQUIVO}"
        senha = f":{self.DB_SENHA}" if self.DB_SENHA else ""
        return (
            f"mysql+pymysql://{self.DB_USUARIO}{senha}"
            f"@{self.DB_HOST}:{self.DB_PORTA}/{self.DB_NOME}?charset=utf8mb4"
        )

    @property
    def arquivo_ddl(self) -> Path:
        return RAIZ / "db" / f"schema_{self.DB_DIALETO}.sql"


@lru_cache
def obter_config() -> Config:
    """Instância única de configuração (recarregada apenas ao reiniciar)."""
    return Config()


config = obter_config()

__all__ = [
    "Config", "config", "obter_config", "RAIZ", "RAIZ_PROJETO", "SECRET_KEY_PADRAO", "PROVEDORES",
]
