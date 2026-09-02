"""入库层：把适配器输出的 DocumentRecord 写入 SQLite（Turso 兼容）。"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Optional, Tuple

from .models import (
    DocumentRecord, DocumentSection, DocumentVersion, LegalDocument,
    to_json, new_id,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """封装所有写库/查询操作。支持本地 sqlite3 文件（Turso 用其 HTTP API 时复用同 SQL）。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
        return self._conn

    def ensure_schema(self, schema_path: str = None):
        """若表不存在则执行 schema.sql 建表。schema_path 默认取工程 db/schema.sql。"""
        import os
        conn = self.connect()
        has = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jurisdictions'"
        ).fetchone()
        if has:
            return
        if schema_path is None:
            # 从 core/ 向上找工程根目录（db/schema.sql 所在处）
            import os
            cur = os.path.dirname(os.path.abspath(__file__))
            while True:
                candidate = os.path.join(cur, "db", "schema.sql")
                if os.path.exists(candidate):
                    schema_path = candidate
                    break
                parent = os.path.dirname(cur)
                if parent == cur:
                    raise FileNotFoundError("找不到 db/schema.sql")
                cur = parent
        with open(schema_path, encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- 法域注册 ----------------------------------------------------------
    def upsert_jurisdiction(self, code: str, name: str, source_type: str,
                            base_url: Optional[str] = None,
                            adapter: str = "npc",
                            config_json: Optional[dict] = None) -> str:
        conn = self.connect()
        jid = conn.execute(
            "SELECT id FROM jurisdictions WHERE code = ?", (code,)
        ).fetchone()
        if jid:
            jid = jid["id"]
            conn.execute(
                "UPDATE jurisdictions SET name=?, source_type=?, base_url=?, adapter=?, "
                "config_json=?, updated_at=? WHERE id=?",
                (name, source_type, base_url, adapter,
                 to_json(config_json), now_iso(), jid),
            )
        else:
            jid = new_id()
            conn.execute(
                "INSERT INTO jurisdictions (id, code, name, source_type, base_url, adapter, "
                "enabled, config_json) VALUES (?,?,?,?,?,?,1,?)",
                (jid, code, name, source_type, base_url, adapter, to_json(config_json)),
            )
        conn.commit()
        return jid

    def get_jurisdiction(self, code: str) -> Optional[sqlite3.Row]:
        return self.connect().execute(
            "SELECT * FROM jurisdictions WHERE code = ?", (code,)
        ).fetchone()

    # -- 写入 ---------------------------------------------------------------
    def _write_doc(self, doc: LegalDocument, conn: sqlite3.Connection) -> str:
        conn.execute(
            """INSERT INTO legal_documents
               (id, jurisdiction_id, doc_key, title, original_title, doc_type, status,
                issuing_body, publish_date, effective_date, repeal_date, language,
                source_url, metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(jurisdiction_id, doc_key) DO UPDATE SET
                 title=excluded.title, original_title=excluded.original_title,
                 doc_type=excluded.doc_type, status=excluded.status,
                 issuing_body=excluded.issuing_body, publish_date=excluded.publish_date,
                 effective_date=excluded.effective_date, repeal_date=excluded.repeal_date,
                 language=excluded.language, source_url=excluded.source_url,
                 metadata_json=excluded.metadata_json, updated_at=excluded.updated_at
            """,
            (doc.id, doc.jurisdiction_id, doc.doc_key, doc.title, doc.original_title,
             doc.doc_type, doc.status, doc.issuing_body, doc.publish_date,
             doc.effective_date, doc.repeal_date, doc.language, doc.source_url,
             to_json(doc.metadata)),
        )
        # upsert 可能命中已存在行（保留旧 id），必须回查真实 id，否则外键会断裂
        row = conn.execute(
            "SELECT id FROM legal_documents WHERE jurisdiction_id=? AND doc_key=?",
            (doc.jurisdiction_id, doc.doc_key),
        ).fetchone()
        return row[0]

    def _write_version(self, ver: DocumentVersion, conn: sqlite3.Connection) -> str:
        conn.execute(
            """INSERT INTO document_versions
               (id, document_id, version_no, effective_from, effective_to,
                version_label, source_ref, is_current)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(document_id, version_no) DO UPDATE SET
                 effective_from=excluded.effective_from, effective_to=excluded.effective_to,
                 version_label=excluded.version_label, source_ref=excluded.source_ref,
                 is_current=excluded.is_current
            """,
            (ver.id, ver.document_id, ver.version_no, ver.effective_from,
             ver.effective_to, ver.version_label, ver.source_ref, ver.is_current),
        )
        # upsert 可能命中已存在行（保留旧 id），回查真实 id，避免条文外键断裂
        row = conn.execute(
            "SELECT id FROM document_versions WHERE document_id=? AND version_no=?",
            (ver.document_id, ver.version_no),
        ).fetchone()
        return row[0]

    def _write_sections(self, sections: Iterable[DocumentSection], conn: sqlite3.Connection):
        for s in sections:
            conn.execute(
                """INSERT INTO document_sections
                   (id, document_id, version_id, section_no, section_type, level_path,
                    heading, body, anchors_json)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(version_id, section_no) DO UPDATE SET
                     section_type=excluded.section_type, level_path=excluded.level_path,
                     heading=excluded.heading, body=excluded.body,
                     anchors_json=excluded.anchors_json
                """,
                (s.id, s.document_id, s.version_id, s.section_no, s.section_type,
                 s.level_path, s.heading, s.body, to_json(s.anchors)),
            )

    def save_record(self, record: DocumentRecord) -> Tuple[str, str]:
        """写入一部法规，返回 (doc_id, version_id)。幂等：重复写入同 doc_key 会更新而非插入。"""
        conn = self.connect()
        try:
            # 1) 先写文档并取回真实 doc_id（upsert 可能保留旧 id）
            doc_id = self._write_doc(record.doc, conn)
            # 2) 就地修正 version/sections 的文档外键，避免指向不存在的行
            record.version.document_id = doc_id
            for s in record.sections:
                s.document_id = doc_id
            # 3) 写版本并取回真实 version_id（upsert 可能保留旧 id）
            ver_id = self._write_version(record.version, conn)
            for s in record.sections:
                s.version_id = ver_id
            # 4) 版本 is_current 互斥：同 doc 其它版本置 0
            conn.execute(
                "UPDATE document_versions SET is_current=0 "
                "WHERE document_id=? AND id<>?",
                (doc_id, ver_id),
            )
            self._write_sections(record.sections, conn)
            conn.commit()
            return doc_id, ver_id
        except Exception:
            conn.rollback()
            raise

    def begin_run(self, jurisdiction_id: str, run_id: str) -> str:
        conn = self.connect()
        rid = new_id()
        conn.execute(
            "INSERT INTO source_records (id, jurisdiction_id, run_id, started_at, status) "
            "VALUES (?,?,?,?, 'running')",
            (rid, jurisdiction_id, run_id, now_iso()),
        )
        conn.commit()
        return rid

    def finish_run(self, record_id: str, status: str, docs_fetched: int,
                   docs_inserted: int, docs_updated: int,
                   cursor_after: Optional[str] = None, error: Optional[str] = None):
        conn = self.connect()
        conn.execute(
            "UPDATE source_records SET finished_at=?, status=?, docs_fetched=?, "
            "docs_inserted=?, docs_updated=?, cursor_after=?, error=? WHERE id=?",
            (now_iso(), status, docs_fetched, docs_inserted, docs_updated,
             cursor_after, error, record_id),
        )
        conn.commit()

    # -- 查询（MCP 层复用） ---------------------------------------------------
    def search_local(self, query: str, limit: int = 10, jurisdiction: Optional[str] = None):
        """条文级全文检索。

        策略：FTS5 trigram 对 ≥3 字的词建索引（子串匹配），但 2 字词查不到；
        因此先按空格/标点切词，≥3 字用 FTS 拿候选 + bm25 排序，
        全部词（含 2 字）再做 LIKE AND 过滤，兼顾召回与精度。
        """
        conn = self.connect()
        words = [w for w in re.split(r"[\s,，。;；、\u3000]+", query) if w]
        if not words:
            return []
        fts_words = [w for w in words if len(w) >= 3]
        # 注意参数顺序：LIKE 参数在前，jurisdiction 参数在后（与 SQL 中 ? 顺序一致）
        like_params = [f"%{w}%" for w in words]
        where = ""
        jur_params: list = []
        if jurisdiction:
            where = " AND s.juris_code = ?"
            jur_params = [jurisdiction]
        like_conds = " AND ".join(["s.body LIKE ?"] * len(words))

        if fts_words:
            # FTS 候选（OR 连接 ≥3 字词）+ 全部词 LIKE 精排
            fts_q = " OR ".join(f'"{w}"' for w in fts_words)
            sql = f"""
                SELECT s.section_id, s.section_no, s.heading, s.body,
                       s.doc_title, s.doc_type, s.status, s.juris_code,
                       s.version_label, s.is_current
                FROM sections_fts f
                JOIN document_sections ds ON ds.rowid = f.rowid
                JOIN v_sections s ON s.section_id = ds.id
                WHERE sections_fts MATCH ? AND {like_conds}{where}
                ORDER BY bm25(sections_fts)
                LIMIT ?
            """
            fts_params = [fts_q] + like_params + jur_params + [limit]
            try:
                rows = conn.execute(sql, fts_params).fetchall()
                if rows:
                    return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass  # 退化到纯 LIKE
        # 无 ≥3 字词，或 FTS 无结果：纯 LIKE（AND 语义）
        sql2 = f"""
            SELECT * FROM v_sections s
            WHERE {like_conds}{where}
            LIMIT ?
        """
        rows = conn.execute(sql2, like_params + jur_params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def get_document(self, doc_id: str):
        conn = self.connect()
        row = conn.execute(
            "SELECT d.*, j.code AS juris_code FROM legal_documents d "
            "JOIN jurisdictions j ON j.id = d.jurisdiction_id WHERE d.id = ?",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        cur = conn.execute(
            "SELECT * FROM document_versions WHERE document_id=? AND is_current=1 "
            "ORDER BY version_no DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        result["current_version"] = dict(cur) if cur else None
        return result

    def get_sections(self, doc_id: str, version_id: Optional[str] = None, limit: int = 200):
        conn = self.connect()
        if version_id:
            rows = conn.execute(
                "SELECT * FROM document_sections WHERE document_id=? AND version_id=? "
                "ORDER BY rowid LIMIT ?",
                (doc_id, version_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT s.* FROM document_sections s "
                "JOIN document_versions v ON v.id=s.version_id "
                "WHERE s.document_id=? AND v.is_current=1 ORDER BY s.rowid LIMIT ?",
                (doc_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_documents(self, jurisdiction: Optional[str] = None, doc_type: Optional[str] = None,
                       status: Optional[str] = None, limit: int = 20):
        conn = self.connect()
        sql = ("SELECT d.*, j.code AS juris_code FROM legal_documents d "
               "JOIN jurisdictions j ON j.id=d.jurisdiction_id WHERE 1=1")
        params: list = []
        if jurisdiction:
            sql += " AND j.code=?"
            params.append(jurisdiction)
        if doc_type:
            sql += " AND d.doc_type=?"
            params.append(doc_type)
        if status:
            sql += " AND d.status=?"
            params.append(status)
        sql += " ORDER BY d.publish_date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
