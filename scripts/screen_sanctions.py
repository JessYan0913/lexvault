#!/usr/bin/env python3
"""批量制裁名单筛查工具。

输入：客户/交易对手名单 CSV（至少含 name 列，可选 aliases 列，用分号分隔多个别名）
输出：筛查报告 CSV（每个名字 → 命中/未命中 + 匹配实体 + 制裁项目 + 风险提示）

匹配逻辑：
  1. 先用库内全文检索（FTS）取候选
  2. 归一化后精确比对 name/aliases（大小写、标点、空格不敏感）
  3. 命中分三类：精确命中（名字完全匹配）、别名命中（匹配到 aka）、宽松命中（FTS 相关但未精确匹配，需人工复核）

用法：
    python3 scripts/screen_sanctions.py --input clients.csv --output report.csv
    python3 scripts/screen_sanctions.py --input clients.csv --jurisdiction us_ofac,eu_sanctions
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.core.store import Store  # noqa: E402
from lexvault.core.aliases import (  # noqa: E402
    CO_TOKEN,
    normalize_enhanced,
    token_set_similar,
)

DEFAULT_JURIS = ["us_ofac", "eu_sanctions"]


def normalize(name: str) -> str:
    """归一化：变音折叠 + 大写 + 去标点 + 公司后缀符号化 + 压缩空白。"""
    return normalize_enhanced(name)


def extract_names(row: dict) -> list[str]:
    """从一行取所有名字（name + aliases 分号分隔）。"""
    names = []
    for key in ("name", "Name", "NAME"):
        if row.get(key):
            names.append(row[key].strip())
            break
    for key in ("aliases", "Aliases", "ALIASES"):
        if row.get(key):
            names.extend(a.strip() for a in row[key].split(";") if a.strip())
    return [n for n in names if n]


def _levenshtein(a: str, b: str) -> int:
    """编辑距离（Levenshtein），用于拼写变体近似匹配。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if min(la, lb) == 0:
        return max(la, lb)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (0 if a[i - 1] == b[j - 1] else 1))
        prev = cur
    return prev[lb]


def _fuzzy_similar(a: str, b: str) -> bool:
    """编辑距离阈值：较长的名字差异 ≤1 字符（含插入/删除/替换）视为近似。
    例如 TALIBAN vs TALEBAN（1 替换）、SMITH vs SMYTH（1 替换）。
    """
    if not a or not b:
        return False
    max_len = max(len(a), len(b))
    if max_len < 5:
        return False
    return _levenshtein(a, b) <= 1


def match_entity(query: str, entity) -> tuple[str, bool]:
    """归一化匹配：query 与实体 heading/body 中的名字比较。

    返回 (匹配级别, 是否命中)。级别：exact / alias / loose / none
    """
    q = normalize(query)
    if not q:
        return ("none", False)
    head = normalize(entity.get("heading") or "")
    body = entity.get("body") or ""
    body_n = normalize(body)

    # 精确：查询名等于实体主名
    if q == head:
        return ("exact", True)
    # 别名：查询名出现在 body（含别名/aka 区），且长度足够避免泛匹配
    if len(q) >= 4 and q in body_n:
        return ("alias", True)
    # 宽松：查询名是主名的子串或反之（≥4 字符），人工复核
    # 注意：① head 为空（纯阿拉伯/中文主名）空串 in q 恒真 ② head 仅剩 _CO_
    # （西里尔名被过滤+OOO 被符号化）_CO_ in q 恒真——都跳过
    q_core = q.replace(CO_TOKEN, "").strip()
    h_core = head.replace(CO_TOKEN, "").strip()
    # 子串匹配要求 ≥6 字符：MULLER(5) 作为子串会误配 MAMULLERI
    if (q_core and h_core and len(q_core) >= 6 and len(h_core) >= 3
            and (q_core in h_core or h_core in q_core)):
        return ("loose", True)
    # 词序无关：查询 token 集是主名 token 集的子集（Hussein Saddam ⊂ Saddam Hussein Al-Tikriti）
    # 要求查询 ≥2 个非后缀 token，避免 Shenzhen Hitech→BIGUANG 误配
    q_tokens = {t for t in q.split() if t and t != CO_TOKEN}
    h_tokens = {t for t in head.split() if t and t != CO_TOKEN}
    if q_tokens and h_tokens and len(q_tokens) >= 2 \
            and q_tokens <= h_tokens:
        return ("loose", True)
    # 拼写变体：编辑距离 ≤1，仅对主名（heading）做（TALIBAN/TALEBAN），人工复核
    # 不对 body 别名做编辑距离模糊（避免“Hitech Trading”误配到叙利亚实体的阿拉伯语名字段）
    if head and _fuzzy_similar(q, head):
        return ("loose", True)
    return ("none", False)


_ENTITY_CACHE: dict[str, list] = {}


def _cached_entities(store: Store, j: str) -> list:
    """缓存法域全部实体（一次性加载，供模糊兜底）。"""
    if j not in _ENTITY_CACHE:
        _ENTITY_CACHE[j] = list(store.iter_entities(j))
    return _ENTITY_CACHE[j]


def screen_name(store: Store, name: str, juris: list[str]) -> list[dict]:
    """筛查单个名字，返回命中实体列表（含匹配级别）。

    两阶段：① FTS 候选精确/别名/子串匹配（快）；
            ② FTS 无候选时，全量遍历 + 编辑距离模糊匹配（抓拼写变体，防漏报）。
    """
    hits = []
    for j in juris:
        try:
            rows = store.search_local(name, limit=8, jurisdiction=j)
        except Exception:
            rows = []
        for r in rows:
            level, matched = match_entity(name, r)
            if matched:
                r["_match_level"] = level
                r["_juris"] = j
                hits.append(r)
        # 兜底：FTS 未命中时全量模糊遍历（拼写变体，如 TALIBAN/Taleban）
        if not rows:
            try:
                for r in _cached_entities(store, j):
                    level, matched = match_entity(name, r)
                    if matched and level == "loose":
                        r["_match_level"] = "loose"
                        r["_juris"] = j
                        hits.append(r)
                        break
            except Exception:
                pass
    return hits


def risk_tag(body: str) -> str:
    """按制裁项目/关键词给风险提示。"""
    b = body.upper()
    tags = []
    if "SECONDARY SANCTIONS RISK" in b:
        tags.append("二级制裁风险")
    if "EO14024" in b or "RUSSIA" in b:
        tags.append("俄罗斯制裁")
    if "IRAN" in b:
        tags.append("伊朗制裁")
    if "CUBA" in b:
        tags.append("古巴制裁")
    if "DPRK" in b or "NORTH KOREA" in b:
        tags.append("朝鲜制裁")
    if "SYRIA" in b:
        tags.append("叙利亚制裁")
    return ";".join(tags) if tags else ""


def main() -> int:
    p = argparse.ArgumentParser(description="批量制裁名单筛查")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--input", required=True, help="客户名单 CSV（name[,aliases]）")
    p.add_argument("--output", default="sanctions_report.csv", help="报告输出路径")
    p.add_argument("--jurisdiction", default=",".join(DEFAULT_JURIS),
                   help="要筛查的法域，逗号分隔（默认 us_ofac,eu_sanctions）")
    args = p.parse_args()

    juris = [j.strip() for j in args.jurisdiction.split(",") if j.strip()]
    store = Store(args.db)
    store.connect()

    with open(args.input, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows_in = list(reader)
    print(f"输入名单: {len(rows_in)} 行，筛查法域: {', '.join(juris)}")

    out_rows = []
    n_hit = n_exact = n_loose = 0
    for row in rows_in:
        names = extract_names(row)
        if not names:
            out_rows.append({**row, "match_status": "NO_NAME", "matched": "",
                             "program": "", "risk_tags": "", "match_level": ""})
            continue
        name = names[0]
        hits = screen_name(store, name, juris)
        if hits:
            # 取最高级别命中（exact > alias > loose）
            hits.sort(key=lambda h: {"exact": 0, "alias": 1, "loose": 2}[h["_match_level"]])
            h0 = hits[0]
            level = h0["_match_level"]
            status = {"exact": "HIT_EXACT", "alias": "HIT_ALIAS", "loose": "HIT_LOOSE"}[level]
            if level in ("exact", "alias"):
                n_hit += 1
            else:
                n_loose += 1
            out_rows.append({
                **row,
                "match_status": status,
                "matched": h0.get("heading", ""),
                "program": h0.get("body", "")[:200],
                "risk_tags": risk_tag(h0.get("body", "")),
                "match_level": level,
                "entity_no": h0.get("section_no", ""),
                "jurisdiction": h0["_juris"],
            })
        else:
            out_rows.append({**row, "match_status": "NO_HIT", "matched": "",
                             "program": "", "risk_tags": "", "match_level": "none",
                             "entity_no": "", "jurisdiction": ""})

    # 输出报告
    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()) if out_rows else ["name"])
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"\n筛查完成：{len(rows_in)} 行")
    print(f"  精确/别名命中: {n_hit} 行")
    print(f"  宽松命中(需复核): {n_loose} 行")
    print(f"  未命中: {len(rows_in) - n_hit - n_loose} 行")
    print(f"报告已写入: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
