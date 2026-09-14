#!/usr/bin/env python3
"""从 opensanctions EU FSF 完整数据集重建 eu_sanctions 法域。

背景：EU FSD webgate 端点（token=dG9rZW4tMjAxNw）只返回部分制度（TERR/IRQ 等 722 实体），
缺少欧盟最核心的俄罗斯/乌克兰制裁制度。opensanctions 的 eu_fsf 数据集覆盖全部制度
（EU-UKR 2947、EU-IRN 684、EU-BLR 372、EU-SYR 371 等，共 6128 实体），且含 JSC ROSOBORONEXPORT。

用法：
    python3 scripts/import_eu_opensanctions.py --local /tmp/eu_os.csv --dry-run
    python3 scripts/import_eu_opensanctions.py --local /tmp/eu_os.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.core.models import (  # noqa: E402
    DocumentRecord, DocumentSection, DocumentVersion, LegalDocument,
)
from lexvault.core.store import Store  # noqa: E402

URL = ("https://data.opensanctions.org/datasets/latest/eu_fsf/targets.simple.csv")


def load_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_record(jid: str, rows: list[dict]) -> DocumentRecord:
    doc = LegalDocument(
        jurisdiction_id=jid,
        doc_key="eu-consolidated-list",
        title="EU Consolidated Financial Sanctions List（欧盟合并金融制裁名单·完整版）",
        original_title="EU Financial Sanctions List (opensanctions eu_fsf)",
        doc_type="other",
        status="in_force",
        issuing_body="European Commission / opensanctions",
        language="en",
        source_url=URL,
        metadata={"source": "opensanctions-eu_fsf", "count": len(rows)},
    )
    ver = DocumentVersion(
        document_id=doc.id, version_no=2,
        version_label="opensanctions eu_fsf 全量（含 EU-UKR 俄罗斯制度）",
        source_ref="targets.simple.csv", is_current=1,
    )
    secs = []
    for i, r in enumerate(rows):
        name = (r.get("name") or "").strip() or f"entity-{i+1}"
        aliases = (r.get("aliases") or "").strip()
        schema = (r.get("schema") or "").strip()
        countries = (r.get("countries") or "").strip()
        addresses = (r.get("addresses") or "").strip()
        programs = (r.get("program_ids") or "").strip()
        birth = (r.get("birth_date") or "").strip()
        ids = (r.get("identifiers") or "").strip()

        parts = []
        if schema:
            parts.append(f"类型: {schema}")
        if programs:
            parts.append(f"项目: {programs}")
        if aliases:
            parts.append(f"别名: {aliases}")
        if countries:
            parts.append(f"国家: {countries}")
        if addresses:
            parts.append(f"地址: {addresses}")
        if birth:
            parts.append(f"出生: {birth}")
        if ids:
            parts.append(f"证件: {ids}")
        body = " | ".join(parts) if parts else name

        secs.append(DocumentSection(
            document_id=doc.id, version_id=ver.id,
            section_no=r.get("id") or f"entity-{i+1}",
            heading=name, body=body, section_type="sanction_entry",
            level_path=None,
            anchors={"program": programs, "schema": schema, "dataset": "eu_fsf"},
        ))
    return DocumentRecord(doc=doc, version=ver, sections=secs)


def main() -> int:
    p = argparse.ArgumentParser(description="导入 opensanctions EU FSF 完整名单")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--local", default="/tmp/eu_os.csv")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rows = load_rows(args.local)
    print(f"CSV 行数: {len(rows)}")

    # 制度统计
    from collections import Counter
    progs = Counter()
    for r in rows:
        for prog in (r.get("program_ids") or "").split(";"):
            if prog:
                progs[prog] += 1
    top = progs.most_common(5)
    print(f"制度 Top5: {top}")

    store = Store(args.db)
    store.connect()
    store.ensure_schema()
    jid = store.upsert_jurisdiction(
        code="eu_sanctions", name="欧盟合并金融制裁名单（完整版）", source_type="api",
        base_url="https://www.opensanctions.org/datasets/eu_fsf/", adapter="eu_opensanctions",
        config_json={"file": "targets.simple.csv"},
    )
    rec = build_record(jid, rows)
    print(f"sections: {len(rec.sections)}")
    if args.dry_run:
        for s in rec.sections[:2]:
            print(f"  {s.section_no}: {s.heading} | {s.body[:90]}")
        return 0
    doc_id, _ = store.save_record(rec)
    print(f"入库完成: doc_id={doc_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
