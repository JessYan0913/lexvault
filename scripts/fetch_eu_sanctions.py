#!/usr/bin/env python3
"""拉取欧盟合并制裁名单入库。

用法：
    python3 scripts/fetch_eu_sanctions.py --dry-run       # 只解析不入库
    python3 scripts/fetch_eu_sanctions.py --local /tmp/eu_fsd.csv   # 用本地 CSV（免下载）
    python3 scripts/fetch_eu_sanctions.py                 # 在线拉取并入库
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.eu_sanctions import (  # noqa: E402
    URL,
    build_record,
    fetch_csv,
    group_entities,
)
from lexvault.core.store import Store  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="拉取欧盟合并制裁名单")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--local", default=None, help="本地 CSV 路径（免在线下载）")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.local:
        import csv
        with open(args.local, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        print(f"本地 CSV: {len(rows)} 行")
    else:
        print("在线拉取 EU FSD CSV ...")
        rows = fetch_csv()
        print(f"拉取: {len(rows)} 行")

    entities = group_entities(rows)
    print(f"实体数: {len(entities)}")

    store = Store(args.db)
    store.connect()
    store.ensure_schema()
    jid = store.upsert_jurisdiction(
        code="eu_sanctions", name="欧盟合并金融制裁名单（EU FSD）", source_type="api",
        base_url="https://webgate.ec.europa.eu/fsd", adapter="eu_sanctions",
        config_json={"file": "csvFullSanctionsList_1_1"},
    )

    rec = build_record(jid, entities)
    print(f"文档: {rec.doc.title}")
    print(f"sections: {len(rec.sections)}")

    if args.dry_run:
        print("[dry-run] 未写库")
        for s in rec.sections[:3]:
            print(f"  {s.section_no}: {s.heading} | {s.body[:90]}")
        return 0

    doc_id, _ = store.save_record(rec)
    print(f"入库完成: doc_id={doc_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
