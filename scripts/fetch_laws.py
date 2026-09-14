#!/usr/bin/env python3
"""拉取全国人大库指定分类全部现行法规（去重 + 排除已废止）。

用法：
    python3 scripts/fetch_laws.py --code 120 --dry-run    # 法律分类 dry-run
    python3 scripts/fetch_laws.py --code 150              # 行政法规入库
    python3 scripts/fetch_laws.py --code 170              # 司法解释入库
    python3 scripts/fetch_laws.py --db /path/to.db

分类 code（flfgCodeId）：
    120 法律 | 150 行政法规 | 170 司法解释 | 140 有关决定 | 200 部门规章

策略：
    - 拉取指定 flfgCodeId 分类全量（翻页）
    - 按标题去重，保留公布日期最新的一条
    - 排除 REJECTED_TITLES（法律分类下已被民法典吸收 / 外资三法废止 等）
    - 跳过库内已有标题（幂等）
    - 下载 docx → 拆条 → save_record 入库
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.adapters.npc import NPCAdapter, strip_highlight, _http_json_waf  # noqa: E402
from lexvault.core.store import Store  # noqa: E402

SEARCH_URL = "https://flk.npc.gov.cn/law-search/search/list"
DEFAULT_HEADERS = {
    "Content-Type": "application/json;charset=utf-8",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Referer": "https://flk.npc.gov.cn/",
}

CATEGORY_NAMES = {110: "基本法律", 120: "法律", 130: "法律(刑事行政)", 140: "法律(经济)",
                 150: "法律(社会)", 155: "法律(新设)", 160: "法律(安全)", 170: "法律(程序)",
                 180: "法律解释/决定", 190: "批准决定", 195: "刑法修正案", 200: "修改决定"}

# 已被民法典吸收 / 已废止，不应入库（主要针对法律 120 分类）
REJECTED_TITLES = {
    "中华人民共和国合同法",      # 民法典合同编取代
    "中华人民共和国担保法",      # 民法典担保制度取代
    "中华人民共和国物权法",      # 民法典物权编取代
    "中华人民共和国婚姻法",      # 民法典婚姻家庭编取代
    "中华人民共和国继承法",      # 民法典继承编取代
    "中华人民共和国收养法",      # 民法典婚姻家庭编取代
    "中华人民共和国民法通则",    # 民法典取代
    "中华人民共和国民法总则",    # 民法典取代
    "中华人民共和国侵权责任法",  # 民法典侵权责任编取代
    "中华人民共和国外资企业法",  # 外商投资法(2020)废止
    "中华人民共和国中外合资经营企业法",  # 外商投资法废止
    "中华人民共和国中外合作经营企业法",  # 外商投资法废止
    "中华人民共和国全民所有制工业企业法",  # 边缘/已失效，暂不纳入
}


def fetch_category(codes: list[int]) -> list[dict]:
    """拉取多个分类全量（翻页直到拿完），合并返回。"""
    all_rows: list[dict] = []
    for code in codes:
        page = 1
        while True:
            body = {
                "searchRange": 1, "sxrq": [], "gbrq": [], "sxx": [], "gbrqYear": [],
                "flfgCodeId": [code], "zdjgCodeId": [],
                "searchContent": "", "pageNum": page, "pageSize": 50, "searchType": 1,
            }
            d = _http_json_waf(SEARCH_URL, "POST", body)
            rows = d.get("rows") or []
            if not rows:
                break
            all_rows.extend(rows)
            total = d.get("total", 0)
            if len(all_rows) >= total or len(rows) < 50:
                break
            page += 1
            time.sleep(1.2)
    return all_rows


def pick_latest(rows: list[dict]) -> list[dict]:
    """按标题去重，保留公布日期最新的一条。"""
    best: dict[str, dict] = {}
    for r in rows:
        t = strip_highlight(r.get("title") or "")
        cur = best.get(t)
        if cur is None or (r.get("gbrq") or "") > (cur.get("gbrq") or ""):
            best[t] = r
    return list(best.values())


def main() -> int:
    p = argparse.ArgumentParser(description="拉取指定分类现行法规（默认补齐全部法律类 code）")
    p.add_argument("--codes", type=str,
                   default="110,120,130,140,150,155,160,170",
                   help="flfgCodeId 列表，逗号分隔（默认补齐法律类全部 code）")
    p.add_argument("--since", type=str, default="",
                   help="只拉取公布日期 >= 该日期的法规（YYYY-MM-DD，默认不过滤）")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--dry-run", action="store_true", help="只列出待拉清单")
    p.add_argument("--limit", type=int, default=0, help="最多拉取 N 部（0=不限）")
    args = p.parse_args()
    codes = [int(c.strip()) for c in args.codes.split(",") if c.strip()]
    cat_name = ",".join(CATEGORY_NAMES.get(c, str(c)) for c in codes)

    store = Store(args.db)
    store.connect()
    store.ensure_schema()
    jid = store.get_jurisdiction("cn")["id"]

    # 1. 拉全量 → 去重取最新
    rows = fetch_category(codes)
    uniq = pick_latest(rows)
    print(f"{cat_name} 全量 {len(rows)} 条记录，去重后 {len(uniq)} 部")

    # 1.5 按公布日期过滤（--since）
    if args.since:
        before = len(uniq)
        uniq = [r for r in uniq if (r.get("gbrq") or "") >= args.since]
        print(f"按公布日期 >= {args.since} 过滤，剩余 {len(uniq)} 部（筛掉 {before - len(uniq)}）")

    # 2. 排除已废止/非正文（按分类定制）
    titles = [strip_highlight(r.get("title") or "") for r in uniq]
    judicial_codes = {320, 330, 340}
    if set(codes) & judicial_codes:
        # 司法解释：保留“关于…的解释/规定/规则”，排除废止目录/修改决定/批复/答复等
        rejected = {
            t for t in titles
            if any(x in t for x in ("废止", "修改", "批准", "目录", "批复", "答复", "草案", "名单", "会议纪要"))
        }
    else:
        # 法律/行政法规：排除修改/批准/关于（决定类）
        rejected = REJECTED_TITLES | {
            t for t in titles if "修改" in t or "批准" in t or "关于" in t
        }
    candidates = [r for r in uniq if strip_highlight(r.get("title") or "") not in rejected]
    print(f"排除已废止/修改决定/批准决定 {len(uniq) - len(candidates)} 部，剩余 {len(candidates)} 部")

    # 3. 跳过库内已有
    existing = {r["title"] for r in store.list_documents(jurisdiction="cn", limit=5000)}
    todo = [r for r in candidates if strip_highlight(r.get("title") or "") not in existing]
    print(f"库内已有 {len(candidates) - len(todo)} 部，待拉 {len(todo)} 部\n")

    if args.limit > 0:
        todo = todo[: args.limit]

    for r in todo:
        t = strip_highlight(r.get("title") or "")
        print(f"  · {r.get('gbrq')}  {t}")

    if args.dry_run:
        print(f"\n[dry-run] 共 {len(todo)} 部待拉，未写库")
        store.close()
        return 0

    # 4. 实际拉取
    adapter = NPCAdapter()
    total_ok = 0
    for i, r in enumerate(todo, 1):
        t = strip_highlight(r.get("title") or "")
        bbbs = r["bbbs"]
        print(f"\n[{i}/{len(todo)}] {t} ({r.get('gbrq')})")
        try:
            # flfgDetails 偶尔会返回 JS 挑战页（非 JSON），此时用搜索结果字段构造 fallback
            try:
                detail = adapter.get_detail(bbbs)
            except Exception as de:
                print(f"  ⚠ 详情接口失败，用搜索结果字段降级: {de}")
                detail = {
                    "title": t, "bbbs": bbbs,
                    "zdjgName": r.get("zdjgName"),
                    "gbrq": r.get("gbrq"), "sxrq": r.get("sxrq"),
                }
            docx_raw = adapter.download_docx(bbbs)
            rec = adapter.to_record(jid, r, detail, docx_raw)
            # 统一 status=in_force（searchRange=1 已是现行有效）
            rec.doc.status = "in_force"
            doc_id, ver_id = store.save_record(rec)
            print(f"  ✅ 条文 {len(rec.sections)} 条")
            total_ok += 1
        except Exception as e:
            print(f"  ✗ 失败: {e}")
        time.sleep(2.0)  # 每部法规间限速 2s，避免影响对方网站

    store.close()
    print(f"\n完成：成功入库 {total_ok} 部")
    return 0


if __name__ == "__main__":
    sys.exit(main())
