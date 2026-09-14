"""美国法典 US Code 适配器（govinfo content/pkg HTML）。

数据源：https://www.govinfo.gov/content/pkg/USCODE-{year}-title{N}/html/USCODE-{year}-title{N}.htm
    HTML 每节以 <div class="section-head"> 标记。

设计：每个 Title = 1 部文档（doc_key=uscode-title-N），每 section = 1 条条文。
"""
from __future__ import annotations

import html as html_lib
import re
import urllib.request
from typing import Optional

from lexvault.core.models import (
    DocumentRecord,
    DocumentSection,
    DocumentVersion,
    LegalDocument,
)

BASE = "https://www.govinfo.gov/content/pkg"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _get(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_title_html(year: str, title_no: int) -> str:
    url = f"{BASE}/USCODE-{year}-title{title_no}/html/USCODE-{year}-title{title_no}.htm"
    return _get(url).decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    text = html_lib.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_title_html(html: str) -> tuple[str, list[dict]]:
    """解析 Title HTML → (title 名, [section dict 列表])。

    section dict: {no, heading, body}
    """
    title_m = re.search(r"<title>([^<]*)</title>", html)
    full_title = html_lib.unescape(title_m.group(1).strip()) if title_m else ""

    # section-head 切块（h3/h4/p 均可，含 &sect; HTML 实体）
    head_re = re.compile(r'<(?:h[1-6]|p|div)\s+class="section-head"[^>]*>(.*?)</(?:h[1-6]|p|div)>', re.S)
    matches = list(head_re.finditer(html))
    sections: list[dict] = []
    for i, m in enumerate(matches):
        heading = _clean(m.group(1))
        no_m = re.match(r"(?:§|&sect;)\s*(\d+[A-Za-z]*\.?\d*)", heading)
        no = no_m.group(1) if no_m else f"s{i+1}"
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        # 截取该节正文：section-head 结束到下一节开始之间，去掉尾部的导航等
        chunk = html[m.end():end]
        # 去掉明显非正文的部分（class 引用块、侧栏）
        chunk = re.sub(r'<div class="[^"]*(footnote|citation|class|ref)[^"]*">.*?</div>', " ", chunk, flags=re.S)
        body = _clean(chunk)
        if not body:
            body = heading
        sections.append({"no": no, "heading": heading, "body": body})
    return full_title, sections


def build_record(jurisdiction_id: str, year: str, title_no: int,
                 title: str, sections: list[dict]) -> DocumentRecord:
    doc = LegalDocument(
        jurisdiction_id=jurisdiction_id,
        doc_key=f"uscode-title-{title_no}",
        title=f"US Code Title {title_no} — {title}",
        original_title=title,
        doc_type="code",
        status="in_force",
        issuing_body="US House of Representatives, Office of the Law Revision Counsel",
        language="en",
        source_url=f"{BASE}/USCODE-{year}-title{title_no}/html/USCODE-{year}-title{title_no}.htm",
        metadata={"title_no": title_no, "year": year, "source": "govinfo"},
    )
    version = DocumentVersion(
        document_id=doc.id,
        version_no=1,
        version_label=f"US Code {year}",
        source_ref=f"USCODE-{year}-title{title_no}",
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
            anchors={"title_no": title_no, "year": year},
        )
        for s in sections
    ]
    return DocumentRecord(doc=doc, version=version, sections=secs)
