#!/usr/bin/env python3
"""入库 CISG（PDF 解析）+ 纽约公约（un.org 简体 HTML）。

CISG：/tmp/cisg_zh.pdf 已用 pymupdf 切好 101 条
纽约公约：un.org 简体官方文本（繁体 PDF 条文号提取不稳）
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fitz  # noqa: E402

from lexvault.core.models import (  # noqa: E402
    DocumentRecord, DocumentSection, DocumentVersion, LegalDocument,
)
from lexvault.core.store import Store  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "Chrome/126.0 Safari/537.36")


def extract_cisg() -> list[tuple[str, str]]:
    doc = fitz.open("/tmp/cisg_zh.pdf")
    pages = []
    for page in doc:
        blocks = page.get_text("blocks")
        blocks.sort(key=lambda b: (round(b[1] / 20), b[0]))
        pages.append("\n".join(b[4] for b in blocks))
    text = "\n".join(pages)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"[ \t]+", " ", text)

    # 非行首匹配 + 编号去重（跨页条文号可能不在行首）
    pat = re.compile(r"第\s*(\d+)\s*条")
    matches = list(pat.finditer(text))
    arts = []
    seen = set()
    for m in matches:
        no = int(m.group(1))
        after = text[m.end():m.end() + 40]
        if "....." in after or "……" in after:  # 目录行
            continue
        if no in seen:
            continue
        seen.add(no)
        arts.append((no, m.end()))
    arts.sort(key=lambda x: x[0])
    out = []
    for i, (no, end) in enumerate(arts):
        body_end = arts[i + 1][1] if i + 1 < len(arts) else len(text)
        body = text[end:body_end].strip()
        body = re.sub(r"\n+", " ", body).strip()
        out.append((f"第{no}条", body))
    return out


def fetch_nyc_zh() -> list[tuple[str, str]]:
    """抓取 un.org 纽约公约简体全文，按'第X条'切分。"""
    url = "https://www.un.org/zh/documents/treaty/UNCITRAL-1958-2"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", errors="replace")

    # 提取正文区（从"第一条"开始），去标签；end 取后半段稳定位置（避免误切）
    start = html.find("第一条")
    body = html[start:] if start >= 0 else html
    # 截到公约文本自然结束（找最后一个条文的结束或公文结束标记）
    tail = body.find("第十六条")
    if tail >= 0:
        # 保留第十六条及之后一小段即可
        body = body[: body.find("</p>", tail) + 4] if body.find("</p>", tail) > 0 else body

    # 按"第X条"切分（中文数字）：交叉引用多，取每个编号后文最长的一次（真实条文）
    cn = "一二三四五六七八九十百零两"
    pat = re.compile(r"第([一二三四五六七八九十]+)条")
    best: dict[str, tuple[int, int]] = {}  # no -> (length, end_pos)
    for m in pat.finditer(body):
        no = m.group(1)
        # 下一个条文号位置作为边界（按原始位置）
        nxt = pat.search(body, m.end())
        body_end = nxt.start() if nxt else len(body)
        chunk = body[m.end():body_end]
        text = re.sub(r"<[^>]+>", " ", chunk)
        text = re.sub(r"\s+", " ", text).strip()
        if no not in best or len(text) > best[no][0]:
            best[no] = (len(text), text)
    arts = [(f"第{no}条", txt) for no, (_, txt) in sorted(best.items(), key=lambda kv: kv[0])]
    return arts


def build_rec(jid: str, doc_key: str, title: str, orig: str, source_url: str,
              arts: list[tuple[str, str]], issuing: str) -> DocumentRecord:
    doc = LegalDocument(
        jurisdiction_id=jid, doc_key=doc_key, title=title, original_title=orig,
        doc_type="other", status="in_force", issuing_body=issuing, language="zh",
        source_url=source_url,
        metadata={"type": "international-convention", "articles": len(arts)},
    )
    ver = DocumentVersion(document_id=doc.id, version_no=1,
                          version_label="官方中文版", source_ref=source_url, is_current=1)
    secs = [
        DocumentSection(document_id=doc.id, version_id=ver.id, section_no=no,
                        heading=no, body=b, section_type="article",
                        level_path=None, anchors={})
        for no, b in arts
    ]
    return DocumentRecord(doc=doc, version=ver, sections=secs)


def main() -> int:
    store = Store(os.path.join(ROOT, "db", "lexvault.db"))
    store.connect(); store.ensure_schema()
    jid = store.upsert_jurisdiction("cn", "中国（全国人大）", "api",
                                    "https://flk.npc.gov.cn", "npc", "{}")

    # CISG
    cisg = extract_cisg()
    print(f"CISG: {len(cisg)} 条")
    rec = build_rec(jid, "intl-cisg", "联合国国际货物销售合同公约",
                    "United Nations Convention on Contracts for the International Sale of Goods (CISG)",
                    "https://uncitral.un.org/zh/texts/salegoods/conventions/sale_of_goods/cisg",
                    cisg, "UNCITRAL")
    doc_id, _ = store.save_record(rec)
    print(f"  入库: {doc_id[:8]} | {len(rec.sections)} 条")

    # 纽约公约（un.org 简体）
    nyc = fetch_nyc_zh()
    print(f"纽约公约: {len(nyc)} 条")
    rec2 = build_rec(jid, "intl-new-york-convention",
                     "承认及执行外国仲裁裁决公约（纽约公约）",
                     "Convention on the Recognition and Enforcement of Foreign Arbitral Awards",
                     "https://www.un.org/zh/documents/treaty/UNCITRAL-1958-2",
                     nyc, "United Nations")
    doc_id2, _ = store.save_record(rec2)
    print(f"  入库: {doc_id2[:8]} | {len(rec2.sections)} 条")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
