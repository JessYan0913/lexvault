"""NPC 适配器测试（不联网：复用已有 db/lexvault.db 的真实入库数据验证）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lexvault.core.store import Store
from lexvault.adapters.npc import split_sections, strip_highlight, _parse_docx_text

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "lexvault.db")


def test_split_sections():
    text = "第一条　为了保护民事主体的合法权益……\n" \
           "第二条　民事主体在民事活动中的法律地位一律平等。\n" \
           "第二章　自然人\n" \
           "第十三条　自然人从出生时起到死亡时止，具有民事权利能力。\n"
    secs = split_sections(text)
    assert [n for n, _ in secs] == ["第一条", "第二条", "第十三条"], secs
    # 第一条同段正文应被保留
    assert "保护民事主体" in secs[0][1]
    print("✅ test_split_sections")


def test_strip_highlight():
    assert strip_highlight("<em class='highlight'>民法典</em>全文") == "民法典全文"
    print("✅ test_strip_highlight")


def test_real_docx_parse():
    """用真实 docx 验证拆条（文件在 /tmp，若不存在则跳过）。"""
    p = "/tmp/npc_minfa.docx"
    if not os.path.exists(p):
        print("⏭ test_real_docx_parse: 跳过（无 /tmp/npc_minfa.docx）")
        return
    raw = open(p, "rb").read()
    text = _parse_docx_text(raw)
    secs = split_sections(text)
    assert len(secs) > 1000, f"民法典应拆出 1000+ 条，实际 {len(secs)}"
    first = [b for n, b in secs if n == "第一条"][0]
    assert "民事主体" in first
    print(f"✅ test_real_docx_parse: 拆出 {len(secs)} 条")


def test_store_real_data():
    """库中应有真实入库的民法典（1260 条文）+ FTS/LIKE 检索均可用。"""
    store = Store(DB)
    store.connect()
    rows = store.connect().execute(
        "SELECT count(*) FROM document_sections s "
        "JOIN legal_documents d ON d.id=s.document_id "
        "WHERE d.title='中华人民共和国民法典'"
    ).fetchone()
    assert rows[0] >= 1260, f"条文数 {rows[0]}"
    hits = store.search_local("居住权", limit=3, jurisdiction="cn")
    assert len(hits) >= 1
    hits2 = store.search_local("抵押", limit=3, jurisdiction="cn")  # 2 字走 LIKE 兜底
    assert len(hits2) >= 1
    print(f"✅ test_store_real_data: 民法典条文 {rows[0]}，检索 居住权/抵押 均命中")


if __name__ == "__main__":
    test_split_sections()
    test_strip_highlight()
    test_real_docx_parse()
    test_store_real_data()
    print("\n✅ NPC 适配器测试全部通过")
