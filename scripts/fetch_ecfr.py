#!/usr/bin/env python3
"""拉取美国联邦法规 eCFR 涉外相关 Part 入库。

用法：
    python3 scripts/fetch_ecfr.py --dry-run        # 只列目标 part，不入库
    python3 scripts/fetch_ecfr.py --title 31       # 拉 title 31 全部 part
    python3 scripts/fetch_ecfr.py                  # 拉默认涉外清单

默认涉外清单（聚焦涉外业务高频）：
    title 12 银行业/金融
    title 15 出口管制（EAR 等）
    title 22 外交（ITAR 等）
    title 31 金融制裁（OFAC 相关 Part 500-599）
    title 50 野生动植物（CITES 等）
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.ecfr import (  # noqa: E402
    build_record,
    fetch_part_xml,
    get_issue_date,
    get_titles,
    parse_part_xml,
)
from lexvault.core.store import Store  # noqa: E402

# 涉外核心 Part 清单（人工筛选，避免逐 title 请求慢的 structure API）
# (title_no, [part...])
DEFAULT_TARGETS = [
    # Title 31 Money and Finance: Treasury — 金融制裁 OFAC 相关（537/538/595 为保留号，eCFR 无正文，已剔除）
    (31, ["501", "535", "536", "539", "560", "561", "562",
          "566", "578", "579", "587", "594", "596", "597", "598"]),
    # Title 15 Commerce and Foreign Trade — 出口管制 EAR
    # Title 15 Commerce and Foreign Trade — 出口管制 EAR（731/733/737 为保留号，已剔除）
    (15, ["730", "732", "734", "736", "738", "740",
          "742", "744", "746", "748", "750", "760", "762", "764", "772", "774"]),
    # Title 22 Foreign Relations — 武器出口 ITAR
    (22, ["120", "121", "122", "123", "124", "125", "126", "127", "128", "129", "130"]),
    # Title 50 Wildlife and Fisheries — CITES / 濒危物种
    (50, ["23", "17"]),
    # Title 12 Banks and Banking — 银行监管核心
    (12, ["1", "3", "5", "7", "9", "11", "25", "26", "27", "28", "31", "32", "33", "34", "36"]),
]


def get_part_list(title_no: int) -> list[str]:
    """（备用）通过 structure API 获取 title 的 part 列表。"""
    import json
    import urllib.request
    date = get_issue_date(title_no)
    url = f"https://www.ecfr.gov/api/versioner/v1/structure/{date}/title-{title_no}.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    parts = []
    for ch in d.get("children", []):
        for p in ch.get("children", []):
            num = p.get("identifier")
            if num and not p.get("reserved"):
                parts.append(num)
    return parts


def main() -> int:
    p = argparse.ArgumentParser(description="拉取 eCFR 涉外 Part")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--title", type=int, default=None, help="只拉指定 title（覆盖默认清单）")
    p.add_argument("--parts", default=None, help="指定 part 列表，逗号分隔（如 500,501）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-parts", type=int, default=0, help="每个 title 最多拉几个 part（0=不限）")
    args = p.parse_args()

    store = Store(args.db)
    store.connect()
    store.ensure_schema()
    jid = store.upsert_jurisdiction(
        code="us", name="美国联邦法规（CFR/eCFR）", source_type="api",
        base_url="https://www.ecfr.gov", adapter="ecfr", config_json={"version": "v1"},
    )

    if args.title:
        targets = [(args.title, args.parts.split(",") if args.parts else None)]
    else:
        targets = DEFAULT_TARGETS

    total_docs = 0
    for title_no, parts in targets:
        print(f"\n===== Title {title_no} ({get_issue_date(title_no)}) =====")
        if parts is None:
            parts = get_part_list(title_no)

        # 跳过已入库 part
        existing = {
            r["doc_key"] for r in store.list_documents(jurisdiction="us", limit=2000)
            if r.get("doc_key", "").startswith(f"title-{title_no}-")
        }
        todo = [pt for pt in parts if f"title-{title_no}-part-{pt}" not in existing]
        print(f"已入库 {len(parts) - len(todo)}，待拉 {len(todo)}")

        if args.dry_run:
            print("  " + ", ".join(todo[:50]) + ("" if len(todo) <= 50 else f" ... 共{len(todo)}"))
            continue

        for pt in todo:
            try:
                raw = fetch_part_xml(title_no, pt)
                part_title, secs = parse_part_xml(raw)
                if not secs:
                    print(f"  · Part {pt}: 0 sections，跳过")
                    continue
                rec = build_record(jid, title_no, pt, part_title, secs,
                                   date=get_issue_date(title_no))
                doc_id, _ = store.save_record(rec)
                print(f"  ✅ Part {pt}: {part_title[:40]} | {len(secs)} sections")
                total_docs += 1
            except Exception as e:
                print(f"  ✗ Part {pt}: {e}")
            time.sleep(1.2)  # 限速，尊重对方服务器

    store.close()
    print(f"\n完成：新增/更新 {total_docs} 个 part")
    return 0


if __name__ == "__main__":
    sys.exit(main())
