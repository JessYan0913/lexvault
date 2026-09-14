#!/usr/bin/env python3
"""导入 OFAC 合并制裁名单到 lexvault。

用法：
    python3 scripts/fetch_ofac.py --dry-run     # 只拉取解析，不入库
    python3 scripts/fetch_ofac.py               # 拉取并入库
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.ofac import OFACAdapter  # noqa: E402
from lexvault.core.store import Store  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="导入 OFAC 合并制裁名单")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--dry-run", action="store_true", help="只拉取解析，不入库")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    adapter = OFACAdapter()

    print("拉取 CONS_PRIM.CSV ...")
    prim = adapter.fetch_primary()
    print(f"  {len(prim)} 个主实体")
    print("拉取 CONS_ALT.CSV ...")
    alt = adapter.fetch_alt()
    print(f"  {len(alt)} 个别名记录")
    print("拉取 add.csv（SDN 地址/国别）...")
    add = adapter.fetch_add()
    print(f"  {len(add)} 条地址记录")

    store = Store(args.db)
    store.connect()
    store.ensure_schema()

    # 建 OFAC 法域（独立于 us-ecfr）
    jid = store.upsert_jurisdiction(
        code="us_ofac",
        name="美国财政部海外资产控制办公室（OFAC）",
        source_type="api",
        base_url="https://sanctionslistservice.ofac.treas.gov",
        adapter="ofac",
        config_json={"export": "CONS_PRIM.CSV", "add": "add.csv"},
    )
    print(f"法域: us_ofac ({jid})")

    record = OFACAdapter.build_records(jid, prim, alt, add)
    print(f"文档: {record.doc.title}")
    print(f"条数(sections): {len(record.sections)}")

    if args.dry_run:
        print("[dry-run] 未写库")
        return 0

    doc_id, ver_id = store.save_record(record)
    print(f"入库完成: doc_id={doc_id}")
    print(f"版本: {ver_id}")

    # 抽查前 3 条
    for s in record.sections[:3]:
        print(f"  {s.section_no}: {s.heading} | {s.body[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
