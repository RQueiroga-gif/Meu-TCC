-- =====================================================================
-- Assistente de DFD — esquema MySQL 8
-- Equivalente funcional do schema_sqlite.sql. Indicado quando houver mais
-- de um processo escrevendo no banco (por exemplo, com o orquestrador n8n).
-- Requer MySQL 8.0+ (tipo JSON, CHECK constraints e SIGNAL).
-- =====================================================================

CREATE TABLE IF NOT EXISTS usuario (
    id_usuario  INT AUTO_INCREMENT PRIMARY KEY,
    nome        VARCHAR(120)  NOT NULL,
    email       VARCHAR(120)  NOT NULL UNIQUE,
    perfil      VARCHAR(20)   NOT NULL,
    senha_hash  VARCHAR(255)  NOT NULL,
    unidade     VARCHAR(120),
    ativo       TINYINT(1)    NOT NULL DEFAULT 1,
    CONSTRAINT ck_usuario_perfil CHECK (perfil IN ('demandante', 'gestor', 'sistema'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS norma (
    id_norma        INT AUTO_INCREMENT PRIMARY KEY,
    sigla           VARCHAR(40)  NOT NULL UNIQUE,
    tipo            VARCHAR(40)  NOT NULL,
    numero          VARCHAR(20)  NOT NULL,
    ano             INT          NOT NULL,
    ementa          TEXT         NOT NULL,
    orgao           VARCHAR(120),
    data_publicacao DATE,
    vigente         TINYINT(1)   NOT NULL DEFAULT 1,
    url_fonte       VARCHAR(500),
    hash_fonte      CHAR(64),
    data_indexacao  DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS trecho_normativo (
    id_trecho           INT AUTO_INCREMENT PRIMARY KEY,
    id_norma            INT          NOT NULL,
    dispositivo         VARCHAR(120) NOT NULL,
    texto               TEXT         NOT NULL,
    texto_indexado      TEXT         NOT NULL,
    artigo              VARCHAR(20),
    referencia_vetorial VARCHAR(200) NOT NULL UNIQUE,
    vigente             TINYINT(1)   NOT NULL DEFAULT 1,
    CONSTRAINT fk_trecho_norma FOREIGN KEY (id_norma)
        REFERENCES norma (id_norma) ON DELETE CASCADE,
    INDEX ix_trecho_norma (id_norma)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS demanda (
    id_demanda           INT AUTO_INCREMENT PRIMARY KEY,
    id_usuario           INT          NOT NULL,
    unidade_requisitante VARCHAR(120) NOT NULL,
    titulo               VARCHAR(255) NOT NULL,
    descricao            TEXT         NOT NULL,
    data_registro        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    situacao             VARCHAR(20)  NOT NULL DEFAULT 'rascunho',
    CONSTRAINT fk_demanda_usuario FOREIGN KEY (id_usuario) REFERENCES usuario (id_usuario),
    CONSTRAINT ck_demanda_situacao CHECK (situacao IN ('rascunho', 'em_elaboracao', 'concluida'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS documento_dfd (
    id_dfd       INT AUTO_INCREMENT PRIMARY KEY,
    id_demanda   INT         NOT NULL UNIQUE,
    numero       VARCHAR(30),
    exercicio    INT,
    situacao     VARCHAR(20) NOT NULL DEFAULT 'em_elaboracao',
    data_criacao DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dfd_demanda FOREIGN KEY (id_demanda)
        REFERENCES demanda (id_demanda) ON DELETE CASCADE,
    CONSTRAINT ck_dfd_situacao CHECK (situacao IN ('em_elaboracao', 'validado', 'exportado'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS minuta (
    id_minuta     INT AUTO_INCREMENT PRIMARY KEY,
    id_dfd        INT         NOT NULL,
    versao        INT         NOT NULL,
    conteudo      JSON        NOT NULL,
    origem        VARCHAR(10) NOT NULL,
    id_usuario    INT         NOT NULL,
    modelo_llm    VARCHAR(120),
    versao_prompt VARCHAR(20),
    parametros    JSON,
    data_geracao  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_minuta_dfd FOREIGN KEY (id_dfd)
        REFERENCES documento_dfd (id_dfd) ON DELETE CASCADE,
    CONSTRAINT fk_minuta_usuario FOREIGN KEY (id_usuario) REFERENCES usuario (id_usuario),
    CONSTRAINT ck_minuta_origem CHECK (origem IN ('geracao', 'edicao')),
    CONSTRAINT uq_minuta_versao UNIQUE (id_dfd, versao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS minuta_trecho (
    id_minuta    INT NOT NULL,
    id_trecho    INT NOT NULL,
    ordem        INT NOT NULL,
    similaridade DECIMAL(6,4),
    PRIMARY KEY (id_minuta, id_trecho),
    CONSTRAINT fk_mt_minuta FOREIGN KEY (id_minuta)
        REFERENCES minuta (id_minuta) ON DELETE CASCADE,
    CONSTRAINT fk_mt_trecho FOREIGN KEY (id_trecho) REFERENCES trecho_normativo (id_trecho)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS validacao (
    id_validacao   INT AUTO_INCREMENT PRIMARY KEY,
    id_dfd         INT         NOT NULL,
    id_minuta      INT         NOT NULL,
    id_usuario     INT         NOT NULL,
    decisao        VARCHAR(12) NOT NULL,
    justificativa  TEXT,
    data_validacao DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_val_dfd FOREIGN KEY (id_dfd)
        REFERENCES documento_dfd (id_dfd) ON DELETE CASCADE,
    CONSTRAINT fk_val_minuta FOREIGN KEY (id_minuta) REFERENCES minuta (id_minuta),
    CONSTRAINT fk_val_usuario FOREIGN KEY (id_usuario) REFERENCES usuario (id_usuario),
    CONSTRAINT ck_val_decisao CHECK (decisao IN ('aprovado', 'devolvido'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS exportacao (
    id_exportacao INT AUTO_INCREMENT PRIMARY KEY,
    id_dfd        INT         NOT NULL,
    id_minuta     INT         NOT NULL,
    id_usuario    INT         NOT NULL,
    formato       VARCHAR(10) NOT NULL DEFAULT 'docx',
    hash_arquivo  CHAR(64),
    data_hora     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_exp_dfd FOREIGN KEY (id_dfd)
        REFERENCES documento_dfd (id_dfd) ON DELETE CASCADE,
    CONSTRAINT fk_exp_minuta FOREIGN KEY (id_minuta) REFERENCES minuta (id_minuta),
    CONSTRAINT fk_exp_usuario FOREIGN KEY (id_usuario) REFERENCES usuario (id_usuario)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS registro_auditoria (
    id_registro   INT AUTO_INCREMENT PRIMARY KEY,
    id_usuario    INT      NOT NULL,
    id_dfd        INT,
    acao          VARCHAR(60) NOT NULL,
    detalhe       JSON,
    data_hora     DATETIME NOT NULL,
    hash_anterior CHAR(64),
    hash_registro CHAR(64) NOT NULL,
    CONSTRAINT fk_aud_usuario FOREIGN KEY (id_usuario) REFERENCES usuario (id_usuario),
    CONSTRAINT fk_aud_dfd FOREIGN KEY (id_dfd) REFERENCES documento_dfd (id_dfd),
    INDEX ix_auditoria_dfd (id_dfd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Cadeia linear (RNF07): dois registros nunca apontam para o mesmo anterior.
-- O MySQL nao tem CREATE INDEX IF NOT EXISTS; a aplicacao ignora o erro 1061
-- ("indice ja existe") ao reaplicar o esquema.
CREATE UNIQUE INDEX uq_auditoria_encadeamento ON registro_auditoria (hash_anterior);
CREATE INDEX ix_validacao_minuta ON validacao (id_minuta);

-- ---------------------------------------------------------------------
-- RN01 e RN05, equivalentes aos gatilhos do SQLite.
-- Ao reaplicar o esquema, a aplicacao ignora o erro 1359 ("gatilho ja
-- existe"), o que mantem a compatibilidade com todo o MySQL 8.
-- ---------------------------------------------------------------------
-- >>>
CREATE TRIGGER trg_rn01_exportacao
BEFORE INSERT ON exportacao
FOR EACH ROW
BEGIN
    DECLARE v_atual INT;
    DECLARE v_aprovada INT;
    SELECT id_minuta INTO v_atual FROM minuta
        WHERE id_dfd = NEW.id_dfd ORDER BY versao DESC LIMIT 1;
    IF NEW.id_minuta <> v_atual THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'RN01: a exportacao deve usar a versao mais recente da minuta';
    END IF;
    SELECT COUNT(*) INTO v_aprovada FROM validacao
        WHERE id_minuta = NEW.id_minuta AND decisao = 'aprovado';
    IF v_aprovada = 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'RN01: DFD sem validacao humana registrada para esta versao';
    END IF;
END;
-- <<<

-- >>>
CREATE TRIGGER trg_exportacao_situacao
AFTER INSERT ON exportacao
FOR EACH ROW
BEGIN
    UPDATE documento_dfd SET situacao = 'exportado' WHERE id_dfd = NEW.id_dfd;
END;
-- <<<

-- >>>
CREATE TRIGGER trg_validacao_situacao
AFTER INSERT ON validacao
FOR EACH ROW
BEGIN
    IF NEW.decisao = 'aprovado' THEN
        UPDATE documento_dfd SET situacao = 'validado' WHERE id_dfd = NEW.id_dfd;
    END IF;
END;
-- <<<

-- >>>
CREATE TRIGGER trg_auditoria_sem_update
BEFORE UPDATE ON registro_auditoria
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'registro_auditoria e somente-inclusao (RN05)';
END;
-- <<<

-- >>>
CREATE TRIGGER trg_auditoria_sem_delete
BEFORE DELETE ON registro_auditoria
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'registro_auditoria e somente-inclusao (RN05)';
END;
-- <<<
