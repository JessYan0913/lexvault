#!/usr/bin/env python3
"""拉取美国法典 US Code 涉外核心 Title 入库。

用法：
    python3 scripts/fetch_uscode.py --dry-run         # 只列目标
    python3 scripts/fetch_uscode.py --year 2024       # 拉默认涉外清单（2024 版）
    python3 scripts/fetch_uscode.py --title 22        # 拉指定 title
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.uscode import (  # noqa: E402
    build_record,
    fetch_title_html,
    parse_title_html,
)
from lexvault.core.store import Store  # noqa: E402

# 涉外核心 Title 清单
# 50 = 战争与国防（IEEPA 1701、TWEA、ECRA 制裁法源）
# 22 = 外交关系（AECA 军控、外援）
# 15 = 商业与贸易（出口管制、FCPA 部分）
# 31 = 货币与金融（OFAC 授权）
# 19 = 关税
DEFAULT_TITLES = [50, 22, 15, 31, 19]


def main() -> int:
    p = argparse.ArgumentParser(description="拉取 US Code 涉外 Title")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--year", default="2024", help="法典年度（govinfo 包名，默认 2024）")
    p.add_argument("--title", type=int, default=None, help="指定 title")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    store = Store(args.db)
    store.connect()
    store.ensure_schema()
    jid = store.upsert_jurisdiction(
        code="us_code", name="美国法典（US Code）", source_type="api",
        base_url="https://www.govinfo.gov", adapter="uscode",
        config_json={"year": args.year},
    )

    titles = [args.title] if args.title else DEFAULT_TITLES

    # 跳过已入库
    existing = {r.get("doc_key") for r in store.list_documents(jurisdiction="us_code", limit=100)}
    todo = [t for t in titles if f"uscode-title-{t}" not in existing]
    print(f"目标 {len(titles)} 个 title，已入库 {len(titles) - len(todo)}，待拉 {len(todo)}")

    if args.dry_run:
        for t in todo:
            print(f"  title {t}")
        return 0

    total = 0
    for t in todo:
        try:
            print(f"\n===== Title {t} ({args.year}) =====")
            html = fetch_title_html(args.year, t)
            title, secs = parse_title_html(html)
            rec = build_record(jid, args.year, t, title, secs)
            doc_id, _ = store.save_record(rec)
            print(f"  ✅ Title {t}: {len(secs)} sections — {title[:60]}")
            total += 1
        except Exception as e:
            print(f"  ✗ Title {t}: {e}")
        time.sleep(2)  # 限速

    store.close()
    print(f"\n完成：新增/更新 {total} 个 title")
    return 0


if __name__ == "__main__":
    sys.exit(main())
