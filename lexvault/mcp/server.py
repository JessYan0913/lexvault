"""lexvault-mcp：本地法律库查询 MCP 服务（查本地 SQLite 为主，在线 NPC 兜底）。

运行（stdio）：
    python -m lexvault.mcp.server            # 本地默认 db/lexvault.db
    LEXVAULT_DB=/path/to.db python -m lexvault.mcp.server

注册为 MCP server（客户端配置示例见 deploy/README.md）。
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..core.store import Store

# 允许直接 python lexvault/mcp/server.py 运行
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("lexvault-mcp")

mcp = FastMCP("lexvault")

_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "db", "lexvault.db",
)
_db_path = os.environ.get("LEXVAULT_DB", _DEFAULT_DB)
_store_inst: Store | None = None

MAX_ITEMS = 50


def _get_store() -> Store:
    global _store_inst
    if _store_inst is None:
        _store_inst = Store(_db_path)
        _store_inst.connect()
        _store_inst.ensure_schema()
    return _store_inst


def _ok(data: Any, total: int | None = None) -> dict:
    return {
        "ok": True,
        "total": total if total is not None else (len(data) if isinstance(data, list) else 0),
        "data": data,
    }


def _err(message: str) -> dict:
    return {"ok": False, "error": message, "data": []}


def _section_to_dict(s: dict) -> dict:
    """条文行 → 可序列化 dict（去 None 噪音，保留关键字段）。"""
    return {
        "jurisdiction": s.get("juris_code"),
        "doc_title": s.get("doc_title"),
        "doc_type": s.get("doc_type"),
        "status": s.get("status"),
        "section_no": s.get("section_no"),
        "heading": s.get("heading"),
        "body": s.get("body"),
        "version_label": s.get("version_label"),
        "is_current": s.get("is_current"),
        "score": s.get("bm25"),
    }


# ---------------- 本地库查询（主路径） ----------------

@mcp.tool()
def search_local(query: str, limit: int = 10, jurisdiction: str | None = None) -> dict:
    """从本地法律库按全文检索条文（FTS5 trigram + LIKE 兜底，支持中文子串匹配）。

    本地库已收录（优先查这里，通常无需联网）：
    - cn 中国法：宪法/法律/行政法规/司法解释（民法典、仲裁法、民事诉讼法涉外编、
      涉外民事关系法律适用法等）+ 国际公约全文（纽约公约、CISG、UNCITRAL 示范法、
      海牙送达/取证/Apostille 认证公约）
    - us / us_code 美国法：CFR（出口管制 EAR、金融制裁 31CFR、ITAR）+ 美国法典核心 Title
    - us_ofac / eu_sanctions / uk_sanctions：美/欧/英三大制裁名单实体（企业出海合规筛查）
    - eu 欧盟核心法规（制裁条例、双重用途出口管制、GDPR 等）

    用法：query 为检索词（可含空格/标点，多词取 AND）；支持“第X条”条文号精确定位
    （自动兼容中文/阿拉伯数字）；jurisdiction 传法域代码（cn/us/us_code/us_ofac/
    eu_sanctions/uk_sanctions/eu）可过滤；limit 限制返回条数（默认 10，最多 50）。
    返回条文级命中：法规标题、条号、正文、效力状态、法域、发布机关、来源。
    """
    try:
        n = min(max(limit, 1), MAX_ITEMS)
        rows = _get_store().search_local(query, limit=n, jurisdiction=jurisdiction)
        return _ok([_section_to_dict(r) for r in rows], len(rows))
    except Exception as exc:  # noqa: BLE001
        logger.exception("search_local failed")
        return _err(str(exc))


@mcp.tool()
def get_document(doc_id: str) -> dict:
    """按文档 ID 获取一部法规的元数据与当前版本信息。"""
    try:
        doc = _get_store().get_document(doc_id)
        if doc is None:
            return _err(f"未找到文档: {doc_id}")
        return _ok(doc, 1)
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_document failed")
        return _err(str(exc))


@mcp.tool()
def get_sections(doc_id: str, limit: int = 200) -> dict:
    """按文档 ID 获取当前版本的条文列表（默认最多 200 条，可传 limit 扩大）。"""
    try:
        rows = _get_store().get_sections(doc_id, limit=min(max(limit, 1), 2000))
        return _ok(rows, len(rows))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_sections failed")
        return _err(str(exc))


@mcp.tool()
def get_citations(doc_id: str, section_no: str, direction: str = "out", limit: int = 20) -> dict:
    """条文交叉引用查询（法律引用链追踪）。

    direction=out：该条文正文引用了哪些条文（出向）；
    direction=in：同文档内哪些条文引用了给定条文（入向）。
    """
    try:
        store = _get_store()
        if direction == "in":
            rows = store.get_citation_in(doc_id, section_no, limit=min(max(limit, 1), 100))
            return _ok(rows, len(rows))
        rows = store.get_citation_out(doc_id, section_no)
        return _ok(rows, len(rows))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_citations failed")
        return _err(str(exc))


@mcp.tool()
def list_documents(jurisdiction: str | None = None, doc_type: str | None = None,
                   status: str | None = None, limit: int = 20) -> dict:
    """列出本地库中的法规（可按法域 / 类型 / 效力状态过滤）。"""
    try:
        n = min(max(limit, 1), MAX_ITEMS)
        rows = _get_store().list_documents(jurisdiction, doc_type, status, limit=n)
        return _ok(rows, len(rows))
    except Exception as exc:  # noqa: BLE001
        logger.exception("list_documents failed")
        return _err(str(exc))


@mcp.tool()
def stats() -> dict:
    """本地法律库统计：法域数、法规数、版本数、条文数、最近抓取时间。"""
    try:
        st = _get_store().connect()
        j = st.execute("SELECT count(*) FROM jurisdictions").fetchone()[0]
        d = st.execute("SELECT count(*) FROM legal_documents").fetchone()[0]
        v = st.execute("SELECT count(*) FROM document_versions").fetchone()[0]
        s = st.execute("SELECT count(*) FROM document_sections").fetchone()[0]
        last = st.execute(
            "SELECT MAX(started_at) FROM source_records WHERE status='success'"
        ).fetchone()[0]
        return _ok({
            "jurisdictions": j, "documents": d, "versions": v, "sections": s,
            "last_success_run": last, "db": _db_path,
        }, 1)
    except Exception as exc:  # noqa: BLE001
        logger.exception("stats failed")
        return _err(str(exc))


# ---------------- 在线兜底（全国人大库） ----------------

@mcp.tool()
def online_npc_search(keyword: str = "", limit: int = 10) -> dict:
    """在线兜底：直接检索全国人大法律法规数据库（flk.npc.gov.cn）。

    本地库没有覆盖的法规/最新修订可用此工具在线查询。
    keyword 为关键词；limit 限制返回条数（默认 10，最多 50）。
    返回命中列表（含 bbbs 与元数据），如需正文请用 online_npc_fetch。
    """
    try:
        from ..adapters.npc import NPCAdapter
        adapter = NPCAdapter()
        n = min(max(limit, 1), MAX_ITEMS)
        rows = adapter.search(keyword, page=1, page_size=n)
        return _ok(rows, len(rows))
    except Exception as exc:  # noqa: BLE001
        logger.exception("online_npc_search failed")
        return _err(str(exc))


@mcp.tool()
def online_npc_fetch(bbbs: str) -> dict:
    """在线兜底：按 bbbs 从全国人大库抓取一部法规（元数据 + 全文条文）并写入本地库。

    返回入库后的文档信息（doc_id / 条文数）。
    """
    try:
        from ..adapters.npc import NPCAdapter
        from ..core.models import AdapterResult
        adapter = NPCAdapter()
        detail = adapter.get_detail(bbbs)
        docx_raw = adapter.download_docx(bbbs)
        # 构造最小 row 供 to_record 使用
        row = {"bbbs": bbbs, "flfgCodeId": detail.get("flfgCodeId") or 0,
               "sxx": detail.get("sxx"), "zdjgCodeId": detail.get("zdjgCodeId"),
               "score": None}
        rec = adapter.to_record("", row, detail, docx_raw)
        store = _get_store()
        jid = store.upsert_jurisdiction(
            code="cn", name="中国全国人大库", source_type="api",
            base_url="https://flk.npc.gov.cn", adapter="npc", config_json={},
        )
        rec.doc.jurisdiction_id = jid
        doc_id, ver_id = store.save_record(rec)
        return _ok({
            "doc_id": doc_id, "version_id": ver_id,
            "title": rec.doc.title, "sections": len(rec.sections),
        }, 1)
    except Exception as exc:  # noqa: BLE001
        logger.exception("online_npc_fetch failed")
        return _err(str(exc))


if __name__ == "__main__":
    mcp.run()
