#!/usr/bin/env python3
"""lexvault 增量更新脚本：从全国人大库拉取最近公布/修改的法规并入库。

用法：
    python3 scripts/update.py                      # 默认：最近 7 天，写入 db/lexvault.db
    python3 scripts/update.py --db /path/to.db --days 30
    python3 scripts/update.py --dry-run            # 只拉元数据不写库，用于预览/验证
    python3 scripts/update.py --keyword 民法典     # 按关键词全量拉取（初装引导）

设计：
    - 以“公布日期 gbrq 在回看窗口内”为增量判据（NPC 搜索接口支持 gbrq 区间过滤）
    - 窗口起点默认 = 上次成功运行的 started_at（cursor_after 语义），无记录则回看 --days 天
    - 入库走 Store.save_record（按 doc_key 幂等 upsert：已存在则更新，新增则插入）
    - 每次运行写一条 source_records 审计
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta

# 保证可从仓库根目录直接运行
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.npc import NPCAdapter  # noqa: E402
from lexvault.core.store import Store  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="lexvault 增量更新")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"),
                   help="SQLite 库文件路径")
    p.add_argument("--days", type=int, default=7,
                   help="回看窗口（天），无历史成功记录时从今天往前推")
    p.add_argument("--dry-run", action="store_true",
                   help="只拉取元数据、打印计划，不下载正文、不写库")
    p.add_argument("--keyword", default="",
                   help="按关键词搜索（初装/定向拉取用）；默认空=按日期增量")
    p.add_argument("--max-docs", type=int, default=50,
                   help="本次最多处理的法规数（默认 50）")
    p.add_argument("--page-size", type=int, default=20,
                   help="每页条数（NPC 接口上限约 20）")
    return p.parse_args()


def last_success_started_at(store: Store, juris_id: str) -> str | None:
    """取该法域最近一次成功运行的 started_at（作为增量窗口起点）。"""
    row = store.connect().execute(
        "SELECT MAX(started_at) AS t FROM source_records "
        "WHERE jurisdiction_id=? AND status='success'",
        (juris_id,),
    ).fetchone()
    return row["t"] if row and row["t"] else None


def main() -> int:
    args = parse_args()
    store = Store(args.db)
    store.connect()
    store.ensure_schema()

    # 1. 法域
    juris_id = store.upsert_jurisdiction(
        code="cn", name="中国全国人大库", source_type="api",
        base_url="https://flk.npc.gov.cn", adapter="npc",
        config_json={"search_url": "https://flk.npc.gov.cn/law-search/search/list"},
    )

    adapter = NPCAdapter()

    if args.dry_run:
        print(f"[dry-run] 库: {args.db} | 模式: {'关键词' if args.keyword else '日期增量'}"
              f"{args.keyword or ''}")

    # 2. 确定回看窗口
    start = None
    if not args.keyword:
        start = last_success_started_at(store, juris_id)
        if start:
            # 往前再多看 1 天，避免边界漏抓（兼容 Z 结尾的 ISO 时间戳）
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")) - timedelta(days=1)
            start = start_dt.date().isoformat()
            print(f"[增量] 上次成功 {last_success_started_at(store, juris_id)}，窗口起点 {start}")
        else:
            start = (date.today() - timedelta(days=args.days)).isoformat()
            print(f"[增量] 无历史记录，回看 {args.days} 天，起点 {start}")
    end = date.today().isoformat()

    # 3. 抓取候选（关键词模式 / 日期窗口模式）
    candidates: list[dict] = []
    page = 1
    while len(candidates) < args.max_docs:
        try:
            rows = adapter.search(
                keyword=args.keyword, page=page, page_size=args.page_size,
                gbrq=([start, end] if not args.keyword else None),
            )
        except Exception as e:
            print(f"[warn] 第 {page} 页拉取失败: {e}")
            break
        if not rows:
            break
        candidates.extend(rows)
        # 日期窗口模式：一页能拉全，提前退出
        if not args.keyword and len(rows) < args.page_size:
            break
        if page >= 10:  # 保守上限
            break
        page += 1
        time.sleep(0.4)

    # 去重（按 bbbs）
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in candidates:
        if r["bbbs"] not in seen:
            seen.add(r["bbbs"])
            uniq.append(r)
    candidates = uniq[: args.max_docs]

    print(f"[增量] 候选 {len(candidates)} 部（窗口 {start} ~ {end}）")
    if not candidates:
        print("[增量] 无新增，跳过")
        return 0

    if args.dry_run:
        for r in candidates[:20]:
            from lexvault.adapters.npc import strip_highlight
            print(f"  · {r.get('gbrq')}  {strip_highlight(r.get('title') or '')}  (bbbs={r['bbbs'][:12]}…)")
        if len(candidates) > 20:
            print(f"  … 共 {len(candidates)} 部（dry-run 不下载正文）")
        print("[dry-run] 完成，未写库")
        return 0

    # 4. 逐个抓正文并入库
    run_id = f"incr-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    record_id = store.begin_run(juris_id, run_id)
    inserted = updated = failed = 0
    for i, row in enumerate(candidates, 1):
        bbbs = row["bbbs"]
        try:
            detail = adapter.get_detail(bbbs)
            docx_raw = adapter.download_docx(bbbs)
            rec = adapter.to_record(juris_id, row, detail, docx_raw)
            doc_id, ver_id = store.save_record(rec)
            print(f"  [{i}/{len(candidates)}] ✅ {rec.doc.title}（条文 {len(rec.sections)}）")
            inserted += 1
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(candidates)}] ❌ {bbbs}: {e}")
        time.sleep(0.3)

    # 5. 审计
    status = "success" if failed == 0 else "partial"
    store.finish_run(record_id, status,
                     docs_fetched=len(candidates),
                     docs_inserted=inserted,
                     docs_updated=0,
                     cursor_after=end,
                     error=None if failed == 0 else f"{failed} 部失败")
    store.close()
    print(f"[增量] 完成：成功 {inserted} / 失败 {failed} | 审计 {record_id[:8]}…")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
