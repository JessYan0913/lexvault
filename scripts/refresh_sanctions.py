#!/usr/bin/env python3
"""制裁名单统一刷新 + 变更检测。

拉取 OFAC / EU / UK 三名单最新版，以新版本（version_no+1）入库
（保留历史版本），并与上一版本 diff 输出变更报告：
- 新增实体（added）
- 移除实体（removed）
- 信息变更（changed，body 变化）

用法：
    python3 scripts/refresh_sanctions.py                 # 刷新全部三名单
    python3 scripts/refresh_sanctions.py --list ofac     # 只刷新 OFAC
    python3 scripts/refresh_sanctions.py --dry-run       # 只拉取解析不写库
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.core.store import Store  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "Chrome/126.0 Safari/537.36")


def now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fetch_ofac() -> dict:
    """拉取 OFAC 三文件（CONS_PRIM/CONS_ALT/CONS_ADD），返回 build_records 需要的元组。"""
    from lexvault.adapters.ofac import OFACAdapter
    a = OFACAdapter()
    prim = a.fetch_primary()
    alt = a.fetch_alt()
    add = a.fetch_add()
    return {"prim": prim, "alt": alt, "add": add}


def fetch_eu() -> list[dict]:
    """拉取 opensanctions EU FSF 全量名单。"""
    import csv
    import urllib.request
    url = "https://data.opensanctions.org/datasets/latest/eu_fsf/targets.simple.csv"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    raw = urllib.request.urlopen(req, timeout=180).read().decode("utf-8")
    import io
    return list(csv.DictReader(io.StringIO(raw)))


def fetch_uk() -> bytes:
    """拉取 UK OFSI XML。"""
    from lexvault.adapters.uk_sanctions import fetch_xml
    return fetch_xml()


def get_next_version(store: Store, doc_key: str) -> int:
    conn = store.connect()
    r = conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) n FROM document_versions v "
        "JOIN legal_documents d ON d.id = v.document_id WHERE d.doc_key = ?",
        (doc_key,)).fetchone()
    return (r["n"] if r else 0) + 1


def build_ofac_record(jid: str, data: dict, version_no: int) -> tuple:
    from lexvault.adapters.ofac import OFACAdapter
    return OFACAdapter.build_records(jid, data["prim"], data["alt"], data["add"])


def build_eu_record(jid: str, rows: list[dict], version_no: int) -> tuple:
    """用 opensanctions targets.simple.csv 结构直接构建（与 import_eu_opensanctions.py 一致）。"""
    from lexvault.core.models import (  # noqa: F401
        DocumentRecord, DocumentSection, DocumentVersion, LegalDocument,
    )
    from lexvault.adapters.eu_sanctions import build_record as _fsd_build
    # opensanctions 行已含 id/name/aliases 等扁平字段，直接构造成适配器期望的实体 dict
    entities = []
    for i, r in enumerate(rows):
        name = (r.get("name") or "").strip() or f"entity-{i+1}"
        aliases = (r.get("aliases") or "").strip()
        schema = (r.get("schema") or "").strip()
        programs = (r.get("program_ids") or "").strip()
        countries = (r.get("countries") or "").strip()
        addresses = (r.get("addresses") or "").strip()
        birth = (r.get("birth_date") or "").strip()
        idents = (r.get("identifiers") or "").strip()
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
        if idents:
            parts.append(f"证件: {idents}")
        body = " | ".join(parts) if parts else name
        entities.append({
            "logical_id": r.get("id") or f"entity-{i+1}",
            "eu_ref": r.get("id") or f"entity-{i+1}",
            "subject_type": schema,
            "name": name,
            "programme": programs,
            "reg_title": "",
            "body": body,
            "remark": "",
        })
    return _fsd_build(jid, entities)


def build_uk_record(jid: str, raw: bytes, version_no: int) -> tuple:
    from lexvault.adapters.uk_sanctions import build_record, parse_xml
    entities = parse_xml(raw)
    return build_record(jid, entities)


def diff_versions(store: Store, doc_key: str, new_sections: list,
                  label: str) -> dict:
    """对比上一版本与新版 sections，输出变更统计与明细。"""
    conn = store.connect()
    # 上一版本（非当前、编号最大）
    old = conn.execute(
        """SELECT s.section_no, s.heading, s.body FROM document_sections s
           JOIN document_versions v ON v.id = s.version_id
           JOIN legal_documents d ON d.id = v.document_id
           WHERE d.doc_key = ? AND v.is_current = 1
           ORDER BY s.section_no""", (doc_key,)).fetchall()
    old_map = {r["section_no"]: r for r in old}
    new_map = {s.section_no: s for s in new_sections}

    added, removed, changed = [], [], []
    for no, s in new_map.items():
        if no not in old_map:
            added.append({"no": no, "heading": s.heading})
        elif old_map[no]["body"] != s.body:
            changed.append({"no": no, "heading": s.heading})
    for no, r in old_map.items():
        if no not in new_map:
            removed.append({"no": no, "heading": r["heading"]})
    return {"label": label, "added": added, "removed": removed, "changed": changed}


def main() -> int:
    p = argparse.ArgumentParser(description="制裁名单刷新+变更检测")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--list", choices=["ofac", "eu", "uk"], default="all")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    store = Store(args.db)
    store.connect()
    store.ensure_schema()
    label = now_label()

    jobs = []
    if args.list in ("all", "ofac"):
        jobs.append(("us_ofac", "ofac-consolidated", fetch_ofac, build_ofac_record, "OFAC"))
    if args.list in ("all", "eu"):
        jobs.append(("eu_sanctions", "eu-consolidated-list", fetch_eu, build_eu_record, "EU"))
    if args.list in ("all", "uk"):
        jobs.append(("uk_sanctions", "uk-sanctions-list", fetch_uk, build_uk_record, "UK"))

    for jcode, doc_key, fetcher, builder, name in jobs:
        print(f"\n===== 刷新 {name} ({jcode}) =====")
        try:
            data = fetcher()
        except Exception as e:
            print(f"  拉取失败: {e}")
            continue
        print(f"  拉取完成")

        jid = store.upsert_jurisdiction(
            jcode,
            {"us_ofac": "美国制裁名单（OFAC）", "eu_sanctions": "欧盟制裁名单",
             "uk_sanctions": "英国制裁名单"}[jcode],
            "api", "https://example.com", "adapter", "{}")
        version_no = get_next_version(store, doc_key)
        print(f"  新版本号: {version_no}")

        rec = builder(jid, data, version_no) if args.list != "all" or True else None
        # builder 返回 DocumentRecord；统一处理
        if isinstance(rec, tuple):
            rec = rec[0]
        # 强制设置新版本号（build_* 内部默认 version_no=1，会覆盖旧版）
        rec.version.version_no = version_no
        rec.version.version_label = f"snapshot {label}"
        rec.version.source_ref = f"refresh {label}"
        rec.version.is_current = 1
        for s in rec.sections:
            s.version_id = rec.version.id
        print(f"  sections: {len(rec.sections)} (version_no={version_no})")

        if args.dry_run:
            print("  [dry-run] 不写库")
            continue

        diff = diff_versions(store, doc_key, rec.sections, label)
        store.save_record(rec)
        print(f"  已入库（doc={rec.doc.id[:8]}）")
        print(f"  变更: 新增 {len(diff['added'])} / 移除 {len(diff['removed'])} / 变更 {len(diff['changed'])}")
        if diff["added"][:5]:
            print("  新增示例:")
            for x in diff["added"][:5]:
                print(f"    + {x['no']} {x['heading']}")
        if diff["removed"][:5]:
            print("  移除示例:")
            for x in diff["removed"][:5]:
                print(f"    - {x['no']} {x['heading']}")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
