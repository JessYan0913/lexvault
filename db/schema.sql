-- ============================================================================
-- lexvault — 多法域法律数据库 Schema
-- 目标：兼容中国（法律/行政法规/部门规章/司法解释/条约）、美国（US Code/eCFR/CFR）、
--       欧盟（EUR-Lex）、国际条约等多数据源，支持版本回溯与条文级检索。
-- 设计原则：
--   1. 多源兼容：jurisdictions 注册表 + metadata_json 弹性字段
--   2. 版本可追溯：document_versions 保存沿革
--   3. 精确到条文：document_sections 拆条存储，正文只存纯文本
--   4. 来源可审计：source_records 记录每次抓取
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- 1. 法域与数据源注册表
-- 每行一个数据来源，接新源 = 插一行 + 写一个爬虫适配器
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jurisdictions (
    id            TEXT PRIMARY KEY,           -- UUID
    code          TEXT NOT NULL UNIQUE,       -- 法域代码: cn / us / eu / intl ...
    name          TEXT NOT NULL,              -- 显示名，如 "中国全国人大库"
    source_type   TEXT NOT NULL,              -- api / html / pdf / mcp ...
    base_url      TEXT,                       -- 数据源基础 URL
    adapter       TEXT NOT NULL,              -- 爬虫适配器标识，如 "npc"
    enabled       INTEGER NOT NULL DEFAULT 1, -- 1=启用 0=禁用
    config_json   TEXT,                       -- 适配器配置（分页大小、headers 等）
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------------
-- 2. 法规主表（元数据）
-- 一部法规一行；各法域独有属性全部进 metadata_json
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legal_documents (
    id                TEXT PRIMARY KEY,       -- 全局 UUID（自生成，不依赖源外部 ID）
    jurisdiction_id   TEXT NOT NULL REFERENCES jurisdictions(id),
    doc_key           TEXT NOT NULL,          -- 源内稳定标识（如 NPC bbbs、CELEX 编号、eCFR title+part），用于增量去重
    title             TEXT NOT NULL,          -- 标准标题（可含法域语言）
    original_title    TEXT,                   -- 原文标题（保留原生语言）
    doc_type          TEXT,                   -- 法规类型枚举: law/admin_reg/rule/judicial_interpretation/treaty/code/regulation/directive/decision/other
    status            TEXT NOT NULL DEFAULT 'in_force', -- in_force / repealed / superseded / expired / draft
    issuing_body      TEXT,                   -- 公布机关
    publish_date      TEXT,                   -- 公布日期 ISO 8601 (YYYY-MM-DD)
    effective_date    TEXT,                   -- 生效日期
    repeal_date       TEXT,                   -- 废止/失效日期
    language          TEXT NOT NULL DEFAULT 'zh',
    source_url        TEXT,                   -- 来源页面 URL
    metadata_json     TEXT,                   -- 弹性字段: 各法域独有属性（codeId、CELEX、title_num 等）
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (jurisdiction_id, doc_key)
);

CREATE INDEX IF NOT EXISTS idx_docs_juris_date  ON legal_documents(jurisdiction_id, publish_date);
CREATE INDEX IF NOT EXISTS idx_docs_publish     ON legal_documents(publish_date);
CREATE INDEX IF NOT EXISTS idx_docs_status      ON legal_documents(status);

-- ----------------------------------------------------------------------------
-- 3. 版本沿革表
-- 一部法规多版本；默认查最新有效版，可回溯历史
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_versions (
    id             TEXT PRIMARY KEY,           -- UUID
    document_id    TEXT NOT NULL REFERENCES legal_documents(id),
    version_no     INTEGER NOT NULL DEFAULT 1, -- 版本号（1=原版）
    effective_from TEXT,                       -- 本版本生效起始日期
    effective_to   TEXT,                       -- 本版本生效截止日期（NULL=当前版）
    version_label  TEXT,                       -- 版本说明，如 "2018年修正"、"2020年第3次修订"
    source_ref     TEXT,                       -- 来源修订记录引用（如 NPC lsyg 链接）
    is_current     INTEGER NOT NULL DEFAULT 0, -- 1=当前有效版本
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (document_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_versions_doc ON document_versions(document_id);

-- ----------------------------------------------------------------------------
-- 4. 条文表（核心内容表）
-- 每条记录 = 一条/一款/一项；正文只存纯文本
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_sections (
    id            TEXT PRIMARY KEY,            -- UUID
    document_id   TEXT NOT NULL REFERENCES legal_documents(id),
    version_id    TEXT NOT NULL REFERENCES document_versions(id),
    section_no    TEXT NOT NULL,               -- 条号字符串（"第一百二十条" / "§ 78j" / "Article 5"）
    section_type  TEXT NOT NULL DEFAULT 'article', -- article / chapter / section / part / item / annex ...
    level_path    TEXT,                        -- 层级路径 "1.3.2"（编/章/条）
    heading       TEXT,                        -- 条标题（如有）
    body          TEXT NOT NULL,               -- 正文纯文本
    anchors_json  TEXT,                        -- 源条文锚点 ID，用于回链官网
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (version_id, section_no)
);

CREATE INDEX IF NOT EXISTS idx_sections_doc    ON document_sections(document_id);
CREATE INDEX IF NOT EXISTS idx_sections_ver    ON document_sections(document_id, version_id);
CREATE INDEX IF NOT EXISTS idx_sections_path   ON document_sections(level_path);

-- ----------------------------------------------------------------------------
-- 5. 抓取审计表
-- 每次爬虫运行一行，支持增量断点与问题回溯
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_records (
    id              TEXT PRIMARY KEY,          -- UUID
    jurisdiction_id TEXT NOT NULL REFERENCES jurisdictions(id),
    run_id          TEXT NOT NULL,             -- 同一次运行共享的 ID
    started_at      TEXT NOT NULL,             -- 抓取开始时间 ISO 8601
    finished_at     TEXT,                      -- 结束时间
    status          TEXT NOT NULL,             -- running / success / partial / failed
    docs_fetched    INTEGER NOT NULL DEFAULT 0,-- 拉取法规数量
    docs_inserted   INTEGER NOT NULL DEFAULT 0,-- 新增数量
    docs_updated    INTEGER NOT NULL DEFAULT 0,-- 更新数量
    cursor_after    TEXT,                      -- 断点游标（最后处理时间/页码）
    error           TEXT                       -- 错误信息快照
);

CREATE INDEX IF NOT EXISTS idx_source_juris ON source_records(jurisdiction_id, started_at);

-- ----------------------------------------------------------------------------
-- 6. FTS5 全文索引（条文级）
-- 检索命中直接定位到条文；中文用 unicode61 + 外部分词（见 ingest 层）
-- ----------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
    section_no,
    heading,
    body,
    content='document_sections',
    content_rowid='rowid',
    tokenize='trigram'
);

-- 触发器：条文增删改时同步 FTS
CREATE TRIGGER IF NOT EXISTS sections_ai AFTER INSERT ON document_sections BEGIN
    INSERT INTO sections_fts(rowid, section_no, heading, body)
    VALUES (new.rowid, new.section_no, new.heading, new.body);
END;
CREATE TRIGGER IF NOT EXISTS sections_ad AFTER DELETE ON document_sections BEGIN
    INSERT INTO sections_fts(sections_fts, rowid, section_no, heading, body)
    VALUES ('delete', old.rowid, old.section_no, old.heading, old.body);
END;
CREATE TRIGGER IF NOT EXISTS sections_au AFTER UPDATE ON document_sections BEGIN
    INSERT INTO sections_fts(sections_fts, rowid, section_no, heading, body)
    VALUES ('delete', old.rowid, old.section_no, old.heading, old.body);
    INSERT INTO sections_fts(rowid, section_no, heading, body)
    VALUES (new.rowid, new.section_no, new.heading, new.body);
END;

-- ----------------------------------------------------------------------------
-- 视图：条文 + 文档 + 版本 联查快捷视图
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_sections AS
SELECT
    s.id AS section_id,
    s.section_no,
    s.section_type,
    s.level_path,
    s.heading,
    s.body,
    s.document_id,
    d.title AS doc_title,
    d.doc_type,
    d.status,
    d.jurisdiction_id,
    j.code AS juris_code,
    v.version_no,
    v.version_label,
    v.is_current
FROM document_sections s
JOIN legal_documents d ON d.id = s.document_id
JOIN jurisdictions j   ON j.id = d.jurisdiction_id
JOIN document_versions v ON v.id = s.version_id;
