#!/usr/bin/env python3
"""从 UNCITRAL 官方中文 PDF 解析 CISG / 纽约公约，按条文入库。

用法：
    python3 scripts/fetch_intl_conventions.py --local /tmp/cisg_zh.pdf /tmp/nyc_zh.pdf
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fitz as pymupdf  # noqa: E402

from lexvault.core.models import (  # noqa: E402
    DocumentRecord, DocumentSection, DocumentVersion, LegalDocument,
)
from lexvault.core.store import Store  # noqa: E402


def extract(pdf: str) -> str:
    """用 pymupdf 按双栏布局顺序提取文本（正确处理官方中文 PDF）。"""
    doc = pymupdf.open(pdf)
    pages = []
    for page in doc:
        blocks = page.get_text("blocks")
        # 按 y 分带（每 20px 一行带），带内按 x 排序 → 双栏阅读顺序
        blocks.sort(key=lambda b: (round(b[1] / 20), b[0]))
        pages.append("\n".join(b[4] for b in blocks))
    text = "\n".join(pages)
    # 去掉字间空格（如 "第 一 条"）
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


CN_NUM = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
         "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100}


def _cn_to_int(s: str) -> int:
    """中文数字转整数（支持 一~一百零一）。"""
    if not s:
        return 0
    total = 0
    num = 0
    for ch in s:
        if ch in CN_NUM:
            v = CN_NUM[ch]
            if v == 100:
                total += (num or 1) * 100
                num = 0
            elif v == 10:
                total += (num or 1) * 10
                num = 0
            else:
                num = v
        else:
            continue
    return total + num


def split_articles(text: str) -> list[tuple[str, str]]:
    """按 '第X条' 切分，兼容阿拉伯数字和中文数字条文号。
    按编号去重（取首次出现，跳过目录点线行），返回 [(条文号, 正文)]。"""
    pat_num = re.compile(r"第\s*(\d+)\s*条")
    pat_cn = re.compile(r"第\s*([一二三四五六七八九十百零两]+)\s*条")
    matches = []
    for m in pat_num.finditer(text):
        matches.append((int(m.group(1)), m.start(), m.end()))
    for m in pat_cn.finditer(text):
        matches.append((_cn_to_int(m.group(1)), m.start(), m.end()))
    if not matches:
        return []
    # 编号去重：保留每个编号首次出现（且非目录）
    seen: dict[int, tuple[int, int]] = {}
    for no, start, end in sorted(matches, key=lambda x: x[1]):
        if no in seen:
            continue
        after = text[end:end + 40]
        if "....." in after or "……" in after:  # 目录行
            continue
        seen[no] = (start, end)
    arts = []
    for no in sorted(seen):
        start, end = seen[no]
        body_end = seen[next(n for n in sorted(seen) if n > no)][0] if any(
            n > no for n in seen) else len(text)
        body = text[end:body_end].strip()
        body = re.sub(r"\n+", " ", body).strip()
        arts.append((f"第{no}条", body))
    return arts


def build_rec(jid: str, title: str, orig: str, source_url: str,
              arts: list[tuple[str, str]]) -> DocumentRecord:
    doc = LegalDocument(
        jurisdiction_id=jid,
        doc_key="intl-" + re.sub(r"[^a-z0-9]+", "-", orig.lower()).strip("-"),
        title=title,
        original_title=orig,
        doc_type="other",
        status="in_force",
        issuing_body="UNCITRAL",
        language="zh",
        source_url=source_url,
        metadata={"type": "international-convention"},
    )
    ver = DocumentVersion(
        document_id=doc.id, version_no=1, version_label="UNCITRAL 中文版",
        source_ref=source_url, is_current=1,
    )
    secs = [
        DocumentSection(document_id=doc.id, version_id=ver.id,
                        section_no=no, heading=no, body=body,
                        section_type="article", level_path=None, anchors={})
        for no, body in arts
    ]
    return DocumentRecord(doc=doc, version=ver, sections=secs)


def main() -> int:
    p = argparse.ArgumentParser(description="解析 CISG/纽约公约入库存档")
    p.add_argument("--db", default=os.path.join(ROOT, "db", "lexvault.db"))
    p.add_argument("--cisg", default="/tmp/cisg_zh.pdf", help="CISG PDF 路径")
    p.add_argument("--nyc", default="/tmp/nyc_zh.pdf", help="纽约公约 PDF 路径")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    store = Store(args.db); store.connect(); store.ensure_schema()
    jid = store.upsert_jurisdiction("cn", "中国（全国人大）", "api",
                                    "https://flk.npc.gov.cn", "npc", "{}")

    specs = [
        ("cisg", args.cisg, "联合国国际货物销售合同公约",
         "United Nations Convention on Contracts for the International Sale of Goods (CISG)",
         "https://uncitral.un.org/zh/texts/salegoods/conventions/sale_of_goods/cisg"),
        ("nyc", args.nyc, "承认及执行外国仲裁裁决公约（纽约公约）",
         "Convention on the Recognition and Enforcement of Foreign Arbitral Awards (New York Convention)",
         "https://uncitral.un.org/zh/texts/arbitration/conventions/foreign_arbitral_awards"),
    ]

    for key, pdf, title, orig, url in specs:
        if not os.path.exists(pdf):
            print(f"✗ {key}: PDF 不存在 {pdf}")
            continue
        text = extract(pdf)
        arts = split_articles(text)
        print(f"\n{title}: 提取 {len(text)} 字，切分 {len(arts)} 条")
        if arts:
            print(f"  首条: {arts[0][0]} | {arts[0][1][:40]}")
            print(f"  末条: {arts[-1][0]} | {arts[-1][1][:40]}")
        if args.dry_run:
            continue
        rec = build_rec(jid, title, orig, url, arts)
        doc_id, _ = store.save_record(rec)
        print(f"  入库: {doc_id[:8]} | {len(rec.sections)} 条")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
