"""欧盟 EUR-Lex 适配器。

数据源：https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}
    HTML 正文，Article 以 <p class="oj-ti-art">Article N</p> 标记。

设计：每部法规 = 1 部文档（doc_key=celex-{celex}），
每 Article = 1 条条文（section_no=Article N，body=该条正文）。
"""
from __future__ import annotations

import re
import urllib.request
from typing import Optional

from lexvault.core.models import (
    DocumentRecord,
    DocumentSection,
    DocumentVersion,
    LegalDocument,
)

BASE = "https://eur-lex.europa.eu/legal-content/EN/TXT/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_html(celex: str) -> str:
    url = f"{BASE}?uri=CELEX:{celex}"
    return _get(url).decode("utf-8", errors="replace")


def _strip_tags(html: str) -> str:
    # 去掉脚本/样式，再剥标签，压缩空白
    html = re.sub(r"<(script|style)[^>]*>.*?</\\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\\s+", " ", text).strip()
    return text


def parse_html(html: str) -> tuple[str, list[dict]]:
    """解析正文 HTML → (法规标题, [article dict 列表])。"""
    title_m = re.search(r"<title>([^<]*)</title>", html)
    title = title_m.group(1).strip() if title_m else ""

    # 定位正文区域：从第一个 oj-ti-art 开始
    body_start = html.find('class="oj-ti-art"')
    body = html[body_start:] if body_start >= 0 else html

    # 按 Article 标记切块
    art_re = re.compile(r'<p[^>]*class="oj-ti-art"[^>]*>(.*?)</p>', re.S)
    matches = list(art_re.finditer(body))
    if not matches:
        # 无 Article 结构（如决定类），整体作为一条
        return title, [{"no": "1", "heading": title, "body": _strip_tags(body)}]

    articles: list[dict] = []
    for i, m in enumerate(matches):
        art_title = _strip_tags(m.group(1))
        no_m = re.search(r"Article\\s+(\\d+[A-Za-z]?)", art_title)
        no = no_m.group(1) if no_m else str(i + 1)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        art_body = _strip_tags(body[m.end():end])
        articles.append({"no": f"Article {no}", "heading": art_title,
                         "body": art_body})
    return title, articles


def build_record(jurisdiction_id: str, celex: str, title: str,
                 articles: list[dict]) -> DocumentRecord:
    doc = LegalDocument(
        jurisdiction_id=jurisdiction_id,
        doc_key=f"celex-{celex}",
        title=f"EU {title}",
        original_title=title,
        doc_type="law",
        status="in_force",
        issuing_body="European Union",
        language="en",
        source_url=f"{BASE}?uri=CELEX:{celex}",
        metadata={"celex": celex, "source": "eur-lex"},
    )
    version = DocumentVersion(
        document_id=doc.id,
        version_no=1,
        version_label="EUR-Lex HTML",
        source_ref=f"CELEX:{celex}",
        is_current=1,
    )
    secs = [
        DocumentSection(
            document_id=doc.id,
            version_id=version.id,
            section_no=a["no"],
            heading=a["heading"] or None,
            body=a["body"],
            section_type="article",
            anchors={"celex": celex},
        )
        for a in articles
    ]
    return DocumentRecord(doc=doc, version=version, sections=secs)
