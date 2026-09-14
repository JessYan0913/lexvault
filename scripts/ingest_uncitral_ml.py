#!/usr/bin/env python3
"""入库：UNCITRAL 国际商事仲裁示范法（1985 原文 + 2006 修正案，官方中文 PDF）。

来源：https://uncitral.un.org/sites/default/files/media-documents/uncitral/zh/ml-arb-c.pdf
按"第N条"切分正文（定位正文起点后，编号去重取首次）。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fitz  # noqa: E402

from lexvault.core.models import (  # noqa: E402
    DocumentRecord, DocumentSection, DocumentVersion, LegalDocument,
)
from lexvault.core.store import Store  # noqa: E402

PDF = "/tmp/ml_arb_zh.pdf"


def extract() -> str:
    doc = fitz.open(PDF)
    text = ""
    for page in doc:
        blocks = page.get_text("blocks")
        blocks.sort(key=lambda b: (round(b[1] / 20), b[0]))
        text += "\n".join(b[4] for b in blocks)
    return text


def split(text: str) -> list[tuple[str, str]]:
    # 正文起点：第一章.总则 + 第1条.适用范围（pos 2712 对应正文开始）
    start = text.find("第1 条. 适用范围")
    if start < 0:
        start = text.find("适用范围*")
    seg = text[start:]
    # 兼容数字间空格（“第3 3 条”=第33条）与 17A 格式
    pat = re.compile(r"第\s*([0-9A-Z](?:\s*[0-9A-Z])*)\s*条")
    matches = list(pat.finditer(seg))
    arts: dict[str, str] = {}
    for i, m in enumerate(matches):
        no = re.sub(r"\s+", "", m.group(1))  # "3 3" -> "33"
        if not no[0].isdigit():
            continue
        after = seg[m.end():m.end() + 30]
        if "....." in after or "…" in after:
            continue  # 目录行
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(seg)
        body = seg[m.end():body_end].strip()
        body = re.sub(r"\n+", " ", body).strip()
        if len(body) < 30:
            continue
        if no not in arts:  # 编号去重取首次
            arts[no] = body
    # 按编号排序（数值 + 字母后缀）
    def keyfun(kv):
        m = re.match(r"(\d+)([A-Z]?)", kv[0])
        return (int(m.group(1)), m.group(2))
    return [(f"第{no}条", b) for no, b in sorted(arts.items(), key=keyfun)]


def main() -> int:
    text = extract()
    arts = split(text)
    print(f"示范法条文数: {len(arts)}")
    if arts:
        print("  首:", arts[0][0], arts[0][1][:50])
        print("  末:", arts[-1][0], arts[-1][1][:50])

    store = Store(os.path.join(ROOT, "db", "lexvault.db"))
    store.connect(); store.ensure_schema()
    jid = store.upsert_jurisdiction("cn", "中国（全国人大）", "api",
                                    "https://flk.npc.gov.cn", "npc", "{}")

    doc = LegalDocument(
        jurisdiction_id=jid,
        doc_key="uncitral-model-law-arbitration",
        title="联合国国际贸易法委员会国际商事仲裁示范法（1985 年，2006 年修正）",
        original_title="UNCITRAL Model Law on International Commercial Arbitration (1985, amended 2006)",
        doc_type="other",
        status="in_force",
        issuing_body="UNCITRAL",
        language="zh",
        source_url="https://uncitral.un.org/sites/default/files/media-documents/uncitral/zh/ml-arb-c.pdf",
        metadata={"type": "model-law", "year": 1985, "amended": 2006},
    )
    ver = DocumentVersion(document_id=doc.id, version_no=1,
                          version_label="官方中文版", source_ref="ml-arb-c.pdf", is_current=1)
    secs = [
        DocumentSection(document_id=doc.id, version_id=ver.id, section_no=no,
                        heading=no, body=b, section_type="article",
                        level_path=None, anchors={})
        for no, b in arts
    ]
    rec = DocumentRecord(doc=doc, version=ver, sections=secs)
    doc_id, _ = store.save_record(rec)
    print(f"入库成功: {doc.title[:50]}")
    print(f"条数: {len(secs)}")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
