#!/usr/bin/env python3
"""拉取欧盟 EUR-Lex 涉外核心法规入库。

用法：
    python3 scripts/fetch_eurlex.py --dry-run        # 只列清单
    python3 scripts/fetch_eurlex.py --celex 32014R0269   # 拉指定 CELEX
    python3 scripts/fetch_eurlex.py                  # 拉默认涉外清单
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.eurlex import (  # noqa: E402
    build_record,
    fetch_html,
    parse_html,
)
from lexvault.core.store import Store  # noqa: E402

# 涉外核心 CELEX 清单（人工筛选）
DEFAULT_CELEX = {
    "32014R0269": "EU Regulation 269/2014 — 乌克兰领土完整制裁（资产冻结）",
    "32014R0833": "EU Regulation 833/2014 — 俄罗斯制裁（贸易/金融限制）",
    "32021R0821": "EU Regulation 2021/821 — 双重用途物项出口管制",
    "32016R0679": "Regulation (EU) 2016/679 — GDPR 通用数据保护条例",
    "32024R1772": "Regulation (EU) 2024/1772 — 欧盟全球人权制裁（EU Global Human Rights）",
    "32023R2675": "Regulation (EU) 2023/2675 — 欧盟反胁迫工具（Anti-Coercion）",
    "32022R2560": "Regulation (EU) 2022/2560 — 外国补贴条例（FSR）",
}


def main() -> int:
    p = argparse.ArgumentParser(description="拉取 EUR-Lex 涉外法规")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--celex", default=None, help="指定 CELEX 编号")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    store = Store(args.db)
    store.connect()
    store.ensure_schema()
    jid = store.upsert_jurisdiction(
        code="eu", name="欧盟法律（EUR-Lex）", source_type="api",
        base_url="https://eur-lex.europa.eu", adapter="eurlex",
        config_json={"version": "v1"},
    )

    celexes = {args.celex: args.celex} if args.celex else DEFAULT_CELEX

    # 跳过已入库
    existing = {r.get("doc_key") for r in store.list_documents(jurisdiction="eu", limit=1000)}
    todo = {k: v for k, v in celexes.items() if f"celex-{k}" not in existing}

    print(f"目标 {len(celexes)} 部，已入库 {len(celexes) - len(todo)}，待拉 {len(todo)}")
    if args.dry_run:
        for k, desc in todo.items():
            print(f"  {k}: {desc}")
        return 0

    total = 0
    for celex, desc in todo.items():
        try:
            html = fetch_html(celex)
            title, arts = parse_html(html)
            rec = build_record(jid, celex, title, arts)
            doc_id, _ = store.save_record(rec)
            print(f"  ✅ {celex}: {len(arts)} articles — {desc}")
            total += 1
        except Exception as e:
            print(f"  ✗ {celex}: {e}")
        time.sleep(1.5)  # 限速

    store.close()
    print(f"完成：新增/更新 {total} 部")
    return 0


if __name__ == "__main__":
    sys.exit(main())
