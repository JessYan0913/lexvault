#!/usr/bin/env python3
"""重试拉取 0 条文文档（按 bbbs 重新下载 docx 并解析入库）。

用法：
    python3 scripts/retry_empty.py --dry-run   # 列出待重试
    python3 scripts/retry_empty.py             # 重试
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.npc import NPCAdapter, strip_highlight  # noqa: E402
from lexvault.core.store import Store  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="重试 0 条文文档")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    store = Store(args.db)
    store.connect()
    conn = store.connect()

    # 找出 0 条文文档
    rows = conn.execute("""
        SELECT d.id, d.title, d.metadata_json
        FROM legal_documents d
        LEFT JOIN document_sections sec ON sec.document_id = d.id
        GROUP BY d.id HAVING count(sec.id) = 0
        ORDER BY d.title
    """).fetchall()
    print(f"0 条文文档: {len(rows)} 部")

    targets = []
    for r in rows:
        meta = r["metadata_json"] or ""
        m = re.search(r'"bbbs":\s*"([^"]+)"', meta)
        if not m:
            print(f"  ✗ 无 bbbs: {r['title'][:50]}")
            continue
        targets.append((r["id"], r["title"], m.group(1)))

    print(f"有 bbbs 可重试: {len(targets)} 部")
    if args.dry_run:
        for _, t, b in targets:
            print(f"  · {t[:60]} ({b})")
        return 0

    jid = store.get_jurisdiction("cn")["id"]
    adapter = NPCAdapter()
    ok = 0
    for doc_id, title, bbbs in targets:
        print(f"\n▶ {title[:60]}")
        try:
            detail = adapter.get_detail(bbbs)
            docx_raw = adapter.download_docx(bbbs)
            rec = adapter.to_record(jid, {"bbbs": bbbs, "title": title}, detail, docx_raw)
            if not rec.sections:
                print("  ✗ 解析后仍无条文")
                continue
            # 重新入库（幂等 upsert，覆盖原骨架）
            store.save_record(rec)
            print(f"  ✅ {len(rec.sections)} 条")
            ok += 1
        except Exception as e:
            print(f"  ✗ 失败: {str(e)[:100]}")
        time.sleep(1.5)  # 限速

    store.close()
    print(f"\n完成：成功 {ok}/{len(targets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
