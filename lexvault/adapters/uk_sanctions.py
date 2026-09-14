"""英国 OFSI 制裁名单适配器（UK Sanctions List）。

数据源：英国外交部（FCDO）官方 XML
    https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml
    21.8MB，6340 个 Designation（实体），每个含 Names(主名/别名)、
    RegimeName、IndividualEntityShip、SanctionsImposed、地址等。

设计：整个英国名单 = 1 部文档（doc_key=uk-sanctions-list），
每个 Designation = 1 条 section（section_no=UniqueID，heading=主名，
body=类型|制度|措施|别名|地址|备注 等可检索文本）。
"""
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from lexvault.core.models import (
    DocumentRecord,
    DocumentSection,
    DocumentVersion,
    LegalDocument,
)

URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
def _tag(name: str) -> str:
    # 兼容有/无命名空间的 XML：按本地名匹配
    return name


def _local(el: ET.Element) -> str:
    return el.tag.split("}")[-1] if el.tag else ""


def _get(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_xml() -> bytes:
    return _get(URL)


def _txt(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def parse_xml(raw: bytes) -> list[dict]:
    """解析 UK 名单 XML → [entity dict 列表]。

    注意：该 XML 无命名空间，直接用本地 tag 匹配；
    用 find 时按任意层级搜索（iter 匹配）。
    """
    root = ET.fromstring(raw)
    entities = []
    for ds in root.iter("Designation"):
        # 名字（Primary Name / Alias）
        primary = ""
        aliases = []
        for n in ds.iter("Name"):
            name6 = _txt(n.find("Name6"))
            ntype = _txt(n.find("NameType"))
            if not name6:
                continue
            if "Primary" in ntype:
                if not primary:
                    primary = name6
            else:
                aliases.append(name6)
        if not primary and aliases:
            primary = aliases.pop(0)
        if not primary:
            continue

        unique_id = _txt(ds.find("UniqueID"))
        regime = _txt(ds.find("RegimeName"))
        i_e_s = _txt(ds.find("IndividualEntityShip"))
        source = _txt(ds.find("DesignationSource"))
        sanctions = _txt(ds.find("SanctionsImposed"))
        other = _txt(ds.find("OtherInformation"))
        # 制裁理由陈述：含关联实体/子公司信息，对筛查漏报至关重要（可能多层嵌套）
        reasons = ""
        for r in ds.iter("UKStatementofReasons"):
            t = " ".join((r.text or "").split())
            if t and t not in reasons:
                reasons = (reasons + " " + t).strip()
        last_updated = _txt(ds.find("LastUpdated"))

        # 非拉丁名字
        non_latin = []
        for nl in ds.iter("NonLatinName"):
            v = _txt(nl.find("NameNonLatinScript"))
            if v and v not in non_latin:
                non_latin.append(v)

        # 地址
        addrs = []
        for a in ds.iter("Address"):
            parts = []
            for line in a:
                lt = _local(line)
                if lt.startswith("AddressLine"):
                    v = _txt(line)
                    if v and v not in parts:
                        parts.append(v)
                elif lt == "AddressCountry":
                    v = _txt(line)
                    if v and v not in parts:
                        parts.append(v)
            if parts:
                addrs.append(", ".join(parts))

        parts = []
        if i_e_s:
            parts.append(f"类型: {i_e_s}")
        if regime:
            parts.append(f"制度: {regime}")
        if sanctions:
            parts.append(f"制裁措施: {sanctions}")
        if source:
            parts.append(f"来源: {source}")
        if aliases:
            parts.append(f"别名: {'；'.join(aliases)}")
        if non_latin:
            parts.append(f"原名: {'；'.join(non_latin)}")
        if addrs:
            parts.append(f"地址: {' | '.join(addrs[:5])}")
        if other:
            parts.append(f"备注: {other}")
        if reasons:
            parts.append(f"制裁理由: {reasons}")
        body = " | ".join(parts) if parts else primary

        entities.append({
            "unique_id": unique_id,
            "name": primary,
            "body": body,
            "regime": regime,
            "sanctions": sanctions,
            "last_updated": last_updated,
        })
    return entities


def build_record(jurisdiction_id: str, entities: list[dict]) -> DocumentRecord:
    doc = LegalDocument(
        jurisdiction_id=jurisdiction_id,
        doc_key="uk-sanctions-list",
        title="UK Sanctions List（英国制裁名单）",
        original_title="UK Sanctions List (OFSI)",
        doc_type="other",
        status="in_force",
        issuing_body="UK Foreign, Commonwealth & Development Office / OFSI",
        language="en",
        source_url=URL,
        metadata={"source": "fcdo-ofsi", "count": len(entities)},
    )
    version = DocumentVersion(
        document_id=doc.id,
        version_no=1,
        version_label="UK Sanctions List snapshot",
        source_ref="UK-Sanctions-List.xml",
        is_current=1,
    )
    secs = [
        DocumentSection(
            document_id=doc.id,
            version_id=version.id,
            section_no=e["unique_id"],
            heading=e["name"],
            body=e["body"],
            section_type="sanction_entry",
            anchors={"regime": e["regime"], "sanctions": e["sanctions"],
                     "last_updated": e["last_updated"]},
        )
        for e in entities
    ]
    return DocumentRecord(doc=doc, version=version, sections=secs)
