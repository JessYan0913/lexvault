#!/usr/bin/env python3
"""拉取主要法律/法典并入库（全国人大库）。

用法：
    python3 scripts/fetch_major.py --list                     # 列出内置法典
    python3 scripts/fetch_major.py                            # 拉取全部内置法典
    python3 scripts/fetch_major.py --keywords 宪法,刑法       # 按关键词拉取
    python3 scripts/fetch_major.py --db /path/to.db

策略：
    - 每个关键词搜索全国人大库，优先挑"标题=中华人民共和国+关键词"的正文（排除决定/解释/修正案）
    - 下载 docx 全文 → 拆条 → save_record 幂等入库
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.npc import NPCAdapter, strip_highlight  # noqa: E402
from lexvault.core.store import Store  # noqa: E402

# 内置主要法律/法典（按关键词拉取）
DEFAULT_KEYWORDS = [
    "宪法", "刑法", "刑事诉讼法", "民事诉讼法",
    "行政处罚法", "行政复议法", "行政许可法", "行政诉讼法",
    "立法法", "监察法", "公司法", "劳动法", "劳动合同法",
    "反垄断法", "个人信息保护法",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="拉取主要法律/法典入库")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS),
                   help="逗号分隔的关键词列表")
    p.add_argument("--list", action="store_true", help="列出内置法典")
    p.add_argument("--skip-existing", action="store_true", default=True,
                   help="已入库的标题跳过（默认跳过）")
    p.add_argument("--no-skip-existing", action="store_true", help="强制重新拉取")
    p.add_argument("--limit-per-keyword", type=int, default=30,
                   help="每个关键词搜索候选数（默认 30）")
    return p.parse_args()


def is_annex(t: str) -> bool:
    """判断是否为附属文件（决定/解释/修正案等），而非法律正文。"""
    return any(x in t for x in (
        "决定", "解释", "修改", "修正", "批复", "答复", "办法",
        "宣誓", "国家宪法日", "设立", "职责问题", "产生办法",
        "修正案", "公告", "草案",
    ))


def pick_best_row(rows: list[dict], keyword: str) -> dict | None:
    """从搜索结果里挑最匹配的正文（排除决定/解释/修正案等附属文件）。"""
    if not rows:
        return None
    norm = [(strip_highlight(r.get("title") or ""), r) for r in rows]
    body_rows = [(t, r) for t, r in norm if not is_annex(t)]
    if not body_rows:
        body_rows = norm

    # 1) 精确等于 中华人民共和国+关键词 / 关键词
    for t, r in body_rows:
        if t == "中华人民共和国" + keyword:
            return r
    for t, r in body_rows:
        if t == keyword:
            return r
    # 2) 标题含关键词且最短（正文通常比修正案短）
    candidates = [(t, r) for t, r in body_rows if keyword in t]
    if candidates:
        candidates.sort(key=lambda x: (len(x[0]), x[0]))
        return candidates[0][1]
    # 3) 退而求其次第一条
    return norm[0][1]


def main() -> int:
    args = parse_args()
    if args.list:
        print("内置法典关键词:")
        for k in DEFAULT_KEYWORDS:
            print(f"  - {k}")
        return 0

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    store = Store(args.db)
    store.connect()
    store.ensure_schema()

    jid = store.upsert_jurisdiction(
        code="cn", name="中国全国人大库", source_type="api",
        base_url="https://flk.npc.gov.cn", adapter="npc",
        config_json={"search_url": "https://flk.npc.gov.cn/law-search/search/list"},
    )

    adapter = NPCAdapter()

    existing = {
        r["title"] for r in store.list_documents(jurisdiction="cn", limit=500)
    } if args.skip_existing else set()

    total_ok = 0
    for kw in keywords:
        print(f"\n▶ 关键词: {kw}")
        try:
            rows = adapter.search(kw, page=1, page_size=args.limit_per_keyword)
        except Exception as e:
            print(f"  ✗ 搜索失败: {e}")
            continue
        row = pick_best_row(rows, kw)
        if row is None:
            print("  ✗ 无结果")
            continue
        title = strip_highlight(row.get("title") or "")
        if title in existing:
            print(f"  ⏭ 已存在，跳过: {title}")
            continue

        bbbs = row["bbbs"]
        try:
            detail = adapter.get_detail(bbbs)
            docx_raw = adapter.download_docx(bbbs)
            rec = adapter.to_record(jid, row, detail, docx_raw)
            doc_id, ver_id = store.save_record(rec)
            print(f"  ✅ {title} | 条文 {len(rec.sections)} | 公布 {rec.doc.publish_date}")
            total_ok += 1
        except Exception as e:
            print(f"  ✗ 拉取/入库失败 {bbbs}: {e}")
        time.sleep(0.4)

    store.close()
    print(f"\n完成：成功入库 {total_ok} 部")
    return 0


if __name__ == "__main__":
    sys.exit(main())
