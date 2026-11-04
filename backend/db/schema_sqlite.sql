-- =====================================================================
-- Assistente de DFD — esquema SQLite
-- Modelo logico do Quadro 10, com os acrescimos de governanca da Fase 3.
-- As regras RN01 e RN05 sao garantidas por gatilho: nenhum caminho de
-- aplicacao consegue contorna-las.
-- Blocos entre "-- >>>" e "-- <<<" sao enviados ao banco como um comando
-- unico (corpo de gatilho contem ';' internos).
-- =====================================================================

CREATE TABLE IF NOT EXISTS usuario (
    id_usuario   INTEGER PRIMARY KEY AUTOINCREMENT,
    nome         TEXT    NOT NULL,
    email        TEXT    NOT NULL UNIQUE,
    perfil       TEXT    NOT NULL CHECK (perfil IN ('demandante', 'gestor', 'sistema')),
    senha_hash   TEXT    NOT NULL,
    unidade      TEXT,
    ativo        INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1))
);

CREATE TABLE IF NOT EXISTS norma (
    id_norma        INTEGER PRIMARY KEY AUTOINCREMENT,
    sigla           TEXT    NOT NULL UNIQUE,
    tipo            TEXT    NOT NULL,
    numero          TEXT    NOT NULL,
    ano             INTEGER NOT NULL,
    ementa          TEXT    NOT NULL,
    orgao           TEXT,
    data_publicacao TEXT,
    vigente         INTEGER NOT NULL DEFAULT 1 CHECK (vigente IN (0, 1)),
    url_fonte       TEXT,
    hash_fonte      TEXT,
    data_indexacao  TEXT
);

CREATE TABLE IF NOT EXISTS trecho_normativo (
    id_trecho           INTEGER PRIMARY KEY AUTOINCREMENT,
    id_norma            INTEGER NOT NULL REFERENCES norma (id_norma) ON DELETE CASCADE,
    dispositivo         TEXT    NOT NULL,
    texto               TEXT    NOT NULL,
    texto_indexado      TEXT    NOT NULL,
    artigo              TEXT,
    referencia_vetorial TEXT    NOT NULL UNIQUE,
    vigente             INTEGER NOT NULL DEFAULT 1 CHECK (vigente IN (0, 1))
);
CREATE INDEX IF NOT EXISTS ix_trecho_norma ON trecho_normativo (id_norma);

CREATE TABLE IF NOT EXISTS demanda (
    id_demanda           INTEGER PRIMARY KEY AUTOINCREMENT,
    id_usuario           INTEGER NOT NULL REFERENCES usuario (id_usuario),
    unidade_requisitante TEXT    NOT NULL,
    titulo               TEXT    NOT NULL,
    descricao            TEXT    NOT NULL,
    data_registro        TEXT    NOT NULL DEFAULT (datetime('now')),
    situacao             TEXT    NOT NULL DEFAULT 'rascunho'
                         CHECK (situacao IN ('rascunho', 'em_elaboracao', 'concluida'))
);

CREATE TABLE IF NOT EXISTS documento_dfd (
    id_dfd       INTEGER PRIMARY KEY AUTOINCREMENT,
    id_demanda   INTEGER NOT NULL UNIQUE REFERENCES demanda (id_demanda) ON DELETE CASCADE,
    numero       TEXT,
    exercicio    INTEGER,
    situacao     TEXT    NOT NULL DEFAULT 'em_elaboracao'
                 CHECK (situacao IN ('em_elaboracao', 'validado', 'exportado')),
    data_criacao TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS minuta (
    id_minuta     INTEGER PRIMARY KEY AUTOINCREMENT,
    id_dfd        INTEGER NOT NULL REFERENCES documento_dfd (id_dfd) ON DELETE CASCADE,
    versao        INTEGER NOT NULL,
    conteudo      TEXT    NOT NULL,           -- JSON: esquema do art. 8 do Dec. 10.947/2022
    origem        TEXT    NOT NULL CHECK (origem IN ('geracao', 'edicao')),
    id_usuario    INTEGER NOT NULL REFERENCES usuario (id_usuario),
    modelo_llm    TEXT,
    versao_prompt TEXT,
    parametros    TEXT,                       -- JSON
    data_geracao  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (id_dfd, versao)                   -- versionamento sem sobrescrita
);

CREATE TABLE IF NOT EXISTS minuta_trecho (
    id_minuta    INTEGER NOT NULL REFERENCES minuta (id_minuta) ON DELETE CASCADE,
    id_trecho    INTEGER NOT NULL REFERENCES trecho_normativo (id_trecho),
    ordem        INTEGER NOT NULL,
    similaridade REAL,
    PRIMARY KEY (id_minuta, id_trecho)        -- rastreabilidade (RN02)
);

CREATE TABLE IF NOT EXISTS validacao (
    id_validacao   INTEGER PRIMARY KEY AUTOINCREMENT,
    id_dfd         INTEGER NOT NULL REFERENCES documento_dfd (id_dfd) ON DELETE CASCADE,
    id_minuta      INTEGER NOT NULL REFERENCES minuta (id_minuta),
    id_usuario     INTEGER NOT NULL REFERENCES usuario (id_usuario),
    decisao        TEXT    NOT NULL CHECK (decisao IN ('aprovado', 'devolvido')),
    justificativa  TEXT,
    data_validacao TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exportacao (
    id_exportacao INTEGER PRIMARY KEY AUTOINCREMENT,
    id_dfd        INTEGER NOT NULL REFERENCES documento_dfd (id_dfd) ON DELETE CASCADE,
    id_minuta     INTEGER NOT NULL REFERENCES minuta (id_minuta),
    id_usuario    INTEGER NOT NULL REFERENCES usuario (id_usuario),
    formato       TEXT    NOT NULL DEFAULT 'docx',
    hash_arquivo  TEXT,
    data_hora     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS registro_auditoria (
    id_registro   INTEGER PRIMARY KEY AUTOINCREMENT,
    id_usuario    INTEGER NOT NULL REFERENCES usuario (id_usuario),
    id_dfd        INTEGER REFERENCES documento_dfd (id_dfd),
    acao          TEXT    NOT NULL,
    detalhe       TEXT,                       -- JSON
    data_hora     TEXT    NOT NULL,
    hash_anterior TEXT,
    hash_registro TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_auditoria_dfd ON registro_auditoria (id_dfd);
-- Cadeia linear (RNF07): dois registros nunca apontam para o mesmo anterior.
-- Sem isto, duas requisicoes simultaneas bifurcariam a cadeia em silencio.
CREATE UNIQUE INDEX IF NOT EXISTS uq_auditoria_encadeamento
    ON registro_auditoria (hash_anterior);
CREATE INDEX IF NOT EXISTS ix_minuta_dfd ON minuta (id_dfd);
CREATE INDEX IF NOT EXISTS ix_validacao_minuta ON validacao (id_minuta);

-- ---------------------------------------------------------------------
-- RN01: nenhum DFD e exportado sem validacao humana registrada, e a
-- exportacao deve usar exatamente a versao aprovada.
-- ---------------------------------------------------------------------
-- >>>
CREATE TRIGGER IF NOT EXISTS trg_rn01_versao_atual
BEFORE INSERT ON exportacao
FOR EACH ROW
WHEN NEW.id_minuta <> (
    SELECT id_minuta FROM minuta WHERE id_dfd = NEW.id_dfd ORDER BY versao DESC LIMIT 1
)
BEGIN
    SELECT RAISE(ABORT, 'RN01: a exportacao deve usar a versao mais recente da minuta');
END;
-- <<<

-- >>>
CREATE TRIGGER IF NOT EXISTS trg_rn01_validacao
BEFORE INSERT ON exportacao
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM validacao v WHERE v.id_minuta = NEW.id_minuta AND v.decisao = 'aprovado'
)
BEGIN
    SELECT RAISE(ABORT, 'RN01: DFD sem validacao humana registrada para esta versao');
END;
-- <<<

-- >>>
CREATE TRIGGER IF NOT EXISTS trg_exportacao_situacao
AFTER INSERT ON exportacao
FOR EACH ROW
BEGIN
    UPDATE documento_dfd SET situacao = 'exportado' WHERE id_dfd = NEW.id_dfd;
END;
-- <<<

-- >>>
CREATE TRIGGER IF NOT EXISTS trg_validacao_situacao
AFTER INSERT ON validacao
FOR EACH ROW
WHEN NEW.decisao = 'aprovado'
BEGIN
    UPDATE documento_dfd SET situacao = 'validado' WHERE id_dfd = NEW.id_dfd;
END;
-- <<<

-- ---------------------------------------------------------------------
-- RN05 / RNF07: a auditoria e somente-inclusao. A cadeia de hashes torna
-- qualquer adulteracao detectavel por reprocessamento.
-- ---------------------------------------------------------------------
-- >>>
CREATE TRIGGER IF NOT EXISTS trg_auditoria_sem_update
BEFORE UPDATE ON registro_auditoria
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'registro_auditoria e somente-inclusao (RN05)');
END;
-- <<<

-- >>>
CREATE TRIGGER IF NOT EXISTS trg_auditoria_sem_delete
BEFORE DELETE ON registro_auditoria
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'registro_auditoria e somente-inclusao (RN05)');
END;
-- <<<
