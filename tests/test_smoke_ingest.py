"""样例数据入库冒烟测试：验证 models + store 全链路（#19 验收）。"""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lexvault.core.models import (
    LegalDocument, DocumentVersion, DocumentSection, DocumentRecord,
)
from lexvault.core.store import Store

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "lexvault.db")


def main():
    store = Store(DB)
    store.connect()
    store.ensure_schema()

    # 1. 注册两个法域（验证多源兼容）
    cn_id = store.upsert_jurisdiction(
        code="cn", name="中国全国人大库", source_type="api",
        base_url="https://flk.npc.gov.cn", adapter="npc",
        config_json={"search_url": "/law-search/search/list"},
    )
    us_id = store.upsert_jurisdiction(
        code="us", name="美国 eCFR", source_type="api",
        base_url="https://www.ecfr.gov", adapter="ecfr",
        config_json={"title": 5},
    )
    assert cn_id and us_id, "法域注册失败"

    # 2. 构造一部样例法规（民法典模拟：1 元数据 + 1 版本 + 3 条文）
    doc = LegalDocument(
        jurisdiction_id=cn_id,
        doc_key="5c7a2c2f-0001",          # 模拟 NPC bbbs
        title="中华人民共和国民法典（样例）",
        original_title="中华人民共和国民法典",
        doc_type="law",
        status="in_force",
        issuing_body="全国人民代表大会",
        publish_date="2020-05-28",
        effective_date="2021-01-01",
        language="zh",
        source_url="https://flk.npc.gov.cn/detail2.html?ZmY4MDgxODE3MjlkZjY3YzAxNzI5ZDUyNDQyYjA2ZmI",
        metadata={"codeId": "ZmY4MDgxODE3MjlkZjY3YzAxNzI5ZDUyNDQyYjA2ZmI", "level": 1},
    )
    ver = DocumentVersion(
        document_id=doc.id, version_no=1, is_current=1,
        effective_from="2021-01-01", version_label="2020年5月28日通过",
    )
    sections = [
        DocumentSection(doc.id, ver.id, "第一条", "为了保护民事主体的合法权益，调整民事关系，维护社会和经济秩序，适应中国特色社会主义发展要求，弘扬社会主义核心价值观，根据宪法，制定本法。", level_path="1"),
        DocumentSection(doc.id, ver.id, "第二条", "民法调整平等主体的自然人、法人和非法人组织之间的人身关系和财产关系。", level_path="1"),
        DocumentSection(doc.id, ver.id, "第三条", "民事主体的人身权利、财产权利以及其他合法权益受法律保护，任何组织或者个人不得侵犯。", level_path="1"),
    ]
    record = DocumentRecord(doc=doc, version=ver, sections=sections)

    run_id = "smoke-test-001"
    sr_id = store.begin_run(cn_id, run_id)
    doc_id, ver_id = store.save_record(record)
    store.finish_run(sr_id, "success", docs_fetched=1, docs_inserted=1, docs_updated=0)

    # 3. 查询验证：全文检索
    hits = store.search_local("民事主体", limit=5, jurisdiction="cn")
    assert hits, "FTS 检索无结果"
    # 样本中第二、三条都含“民事主体”，任一条命中即可（验证检索链路）
    assert any("民事主体" in h["body"] for h in hits), f"命中内容不含关键词: {[(h['doc_title'], h['section_no']) for h in hits]}"
    print("FTS 命中:", [(h["doc_title"], h["section_no"], h["body"][:18]) for h in hits])

    # 4. 查询验证：取整部法规
    d = store.get_document(doc_id)
    assert d and d["current_version"], "取文档失败"
    secs = store.get_sections(doc_id)
    assert len(secs) == 3, f"条文数期望 3，实际 {len(secs)}"
    print("文档:", d["title"], "| 条文数:", len(secs))

    # 5. 查询验证：列表
    docs = store.list_documents(jurisdiction="cn", doc_type="law")
    print("cn 法律列表:", [(x["title"], x["juris_code"]) for x in docs])

    # 6. 审计表验证
    row = store.connect().execute(
        "SELECT status, docs_inserted FROM source_records WHERE run_id=?", (run_id,)
    ).fetchone()
    assert row and row["status"] == "success", "审计记录失败"
    print("审计记录:", dict(row))

    store.close()
    print("\n✅ 冒烟测试全部通过：数据模型 + 入库层可用")


if __name__ == "__main__":
    main()
