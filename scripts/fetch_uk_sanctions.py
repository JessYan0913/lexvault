#!/usr/bin/env python3
"""拉取英国 OFSI 制裁名单入库。

用法：
    python3 scripts/fetch_uk_sanctions.py --dry-run             # 只解析不入库
    python3 scripts/fetch_uk_sanctions.py --local /tmp/uk_sanctions.xml  # 用本地 XML
    python3 scripts/fetch_uk_sanctions.py                       # 在线拉取并入库
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.uk_sanctions import (  # noqa: E402
    URL,
    build_record,
    fetch_xml,
    parse_xml,
)
from lexvault.core.store import Store  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="拉取英国 OFSI 制裁名单")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--local", default=None, help="本地 XML 路径（免在线下载）")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.local:
        raw = open(args.local, "rb").read()
        print(f"本地 XML: {len(raw)/1024/1024:.1f} MB")
    else:
        print("在线拉取 UK Sanctions List ...")
        raw = fetch_xml()
        print(f"拉取: {len(raw)/1024/1024:.1f} MB")

    entities = parse_xml(raw)
    print(f"实体数: {len(entities)}")

    store = Store(args.db)
    store.connect()
    store.ensure_schema()
    jid = store.upsert_jurisdiction(
        code="uk_sanctions", name="英国制裁名单（UK OFSI）", source_type="api",
        base_url="https://sanctionslist.fcdo.gov.uk", adapter="uk_sanctions",
        config_json={"file": "UK-Sanctions-List.xml"},
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
