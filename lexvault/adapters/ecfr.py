"""美国联邦法规 eCFR 适配器（Code of Federal Regulations, Electronic CFR）。

数据源：https://www.ecfr.gov/api/versioner/v1/
    - /titles.json                          全部 50 个 Title 及生效日期
    - /full/{date}/title-{N}.xml?part={P}   Title 指定 Part 的全文（XML）
    - /search/v1/results?query=...          官方全文搜索（JSON，section 级）

设计：每个 CFR Part = 1 部文档（doc_key=title-NN-part-XXX），
每个 section = 1 条条文（section_no=节号，heading=节标题，body=正文）。
"""
from __future__ import annotations

import gzip
import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from lexvault.core.models import (
    DocumentRecord,
    DocumentSection,
    DocumentVersion,
    LegalDocument,
)

BASE = "https://www.ecfr.gov/api/versioner/v1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# eCFR XML 命名空间
NS = {"cfr": "https://www.ecfr.gov/xml"}


def _get(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip",
                      "Accept": "*/*"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw


def get_titles() -> list[dict]:
    import json
    raw = _get(f"{BASE}/titles.json")
    return json.loads(raw.decode("utf-8")).get("titles", [])


def get_issue_date(title_no: int) -> str:
    """取某 title 的最新生效日期（latest_issue_date）。"""
    for t in get_titles():
        if t.get("number") == title_no:
            d = t.get("latest_issue_date")
            if d:
                return d
    return "current"


def _text(el: ET.Element) -> str:
    """提取元素内全部文本（含子元素），压缩空白。"""
    if el is None:
        return ""
    parts = [el.text or ""] + [ (c.tail or "") for c in el.iter() if c != el ]
    # 更稳妥：收集所有文本节点
    txt = "".join(el.itertext())
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def fetch_part_xml(title_no: int, part: str, date: Optional[str] = None) -> bytes:
    if not date:
        date = get_issue_date(title_no)
    url = f"{BASE}/full/{date}/title-{title_no}.xml?part={part}"
    return _get(url)


def parse_part_xml(raw: bytes) -> tuple[str, list[dict]]:
    """解析 Part XML → (part 标题, [section dict 列表])。

    section dict: {no, heading, body, path, citation}
    """
    root = ET.fromstring(raw)
    # Part 标题：DIV5 > HEAD
    head_el = root.find("HEAD")
    part_title = _text(head_el) if head_el is not None else ""

    sections: list[dict] = []
    for div8 in root.iter("DIV8"):
        no = (div8.get("N") or "").strip()
        head = _text(div8.find("HEAD"))
        body_parts = [_text(p) for p in div8.iter("P")]
        body = "\n".join(p for p in body_parts if p)
        meta = (div8.get("hierarchy_metadata") or "")
        sections.append({
            "no": no,
            "heading": head,
            "body": body,
            "citation": f"{no}",
        })
    return part_title, sections


def build_record(jurisdiction_id: str, title_no: int, part: str,
                 part_title: str, sections: list[dict],
                 date: str) -> DocumentRecord:
    doc = LegalDocument(
        jurisdiction_id=jurisdiction_id,
        doc_key=f"title-{title_no}-part-{part}",
        title=f"CFR Title {title_no}, Part {part} — {part_title}",
        original_title=part_title,
        doc_type="code",
        status="in_force",
        issuing_body="US Office of the Federal Register / eCFR",
        language="en",
        source_url=f"https://www.ecfr.gov/current/title-{title_no}/part-{part}",
        metadata={"title_no": title_no, "part": part, "cfr_date": date},
    )
    version = DocumentVersion(
        document_id=doc.id,
        version_no=1,
        version_label=f"eCFR {date}",
        source_ref=f"{BASE}/full/{date}/title-{title_no}.xml?part={part}",
        is_current=1,
    )
    secs = [
        DocumentSection(
            document_id=doc.id,
            version_id=version.id,
            section_no=s["no"],
            heading=s["heading"] or None,
            body=s["body"],
            section_type="section",
            level_path=f"{part}",
            anchors={"citation": s.get("citation"), "path": f"title-{title_no}/section-{s['no']}"},
        )
        for s in sections
    ]
    return DocumentRecord(doc=doc, version=version, sections=secs)


def search(keyword: str, page: int = 1, per_page: int = 10) -> dict:
    """eCFR 官方全文搜索（在线兜底用）。"""
    import json
    url = (f"{BASE}/../search/v1/results?query={urllib.parse.quote(keyword)}"
           f"&page={page}&per_page={per_page}")
    raw = _get(url)
    return json.loads(raw.decode("utf-8"))
