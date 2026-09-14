"""欧盟合并制裁名单适配器（EU Consolidated Financial Sanctions List）。

数据源：EU FSD 公开 CSV
    https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw
    分号分隔，118 列；同一实体（Entity_LogicalId）有多行（姓名/别名/地址/出生/证件/国籍）。

设计：整个欧盟合并名单 = 1 部文档（doc_key=eu-consolidated-list），
每个实体 = 1 条 section（section_no=EU 参考号，heading=主姓名/机构名，
body=类型|项目|制裁条例|别名|地址|出生|证件|备注 等可检索文本）。
"""
from __future__ import annotations

import csv
import io
import urllib.request
from collections import OrderedDict
from typing import Optional

from lexvault.core.models import (
    DocumentRecord,
    DocumentSection,
    DocumentVersion,
    LegalDocument,
)

URL = ("https://webgate.ec.europa.eu/fsd/fsf/public/files/"
       "csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_csv() -> list[dict]:
    raw = _get(URL)
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    return list(reader)


def _norm(v) -> str:
    return (v or "").strip()


def _collect(rows: list[dict], prefix: str, fields: list[str]) -> str:
    """收集某前缀下的字段值（去空、去重）。"""
    vals = []
    for r in rows:
        for f in fields:
            v = _norm(r.get(f"{prefix}_{f}") or "")
            if v and v not in vals:
                vals.append(v)
    return "；".join(vals)


def group_entities(rows: list[dict]) -> list[dict]:
    """按 Entity_LogicalId 聚合多行成一个实体 dict。"""
    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for r in rows:
        lid = _norm(r.get("Entity_LogicalId"))
        if not lid:
            lid = "unknown"
        grouped.setdefault(lid, []).append(r)

    entities = []
    for lid, grp in grouped.items():
        e0 = grp[0]
        names = []
        seen = set()
        for r in grp:
            wn = _norm(r.get("NameAlias_WholeName"))
            ln = _norm(r.get("NameAlias_LastName"))
            fn = _norm(r.get("NameAlias_FirstName"))
            cands = [wn, (ln + (" " + fn if fn else "")) if ln else "", fn]
            for c in cands:
                if c and c not in seen:
                    seen.add(c)
                    names.append(c)
        primary = names[0] if names else _norm(e0.get("Entity_LogicalId"))
        programme = _collect(grp, "Entity_Regulation", ["Programme"])
        reg_title = _collect(grp, "Entity_Regulation", ["NumberTitle"])
        subj_type = _norm(e0.get("Entity_SubjectType_ClassificationCode"))
        remark = _collect(grp, "Entity", ["Remark", "DesignationDetails"])
        aliases = "；".join(names[1:]) if len(names) > 1 else ""
        # 地址/出生/证件
        addr = _collect(grp, "Address", ["City", "CountryDescription", "Street", "ZipCode"])
        birth = _collect(grp, "BirthDate", ["Year", "Place", "CountryDescription", "BirthDate"])
        idents = _collect(grp, "Identification", ["Number", "TypeDescription", "CountryDescription"])
        citizenship = _collect(grp, "Citizenship", ["CountryDescription"])

        parts = []
        if subj_type:
            parts.append(f"类型: {subj_type}")
        if programme:
            parts.append(f"项目: {programme}")
        if reg_title:
            parts.append(f"制裁条例: {reg_title}")
        if aliases:
            parts.append(f"别名: {aliases}")
        if addr:
            parts.append(f"地址: {addr}")
        if birth:
            parts.append(f"出生: {birth}")
        if idents:
            parts.append(f"证件: {idents}")
        if citizenship:
            parts.append(f"国籍: {citizenship}")
        if remark:
            parts.append(f"备注: {remark}")
        body = " | ".join(parts) if parts else primary

        entities.append({
            "logical_id": lid,
            "eu_ref": _norm(e0.get("Entity_EU_ReferenceNumber")),
            "subject_type": subj_type,
            "name": primary,
            "programme": programme,
            "reg_title": reg_title,
            "body": body,
            "remark": remark,
        })
    return entities


def build_record(jurisdiction_id: str, entities: list[dict]) -> DocumentRecord:
    doc = LegalDocument(
        jurisdiction_id=jurisdiction_id,
        doc_key="eu-consolidated-list",
        title="EU Consolidated Financial Sanctions List（欧盟合并金融制裁名单）",
        original_title="Consolidated List of Persons, Groups and Entities subject to EU Financial Sanctions",
        doc_type="other",
        status="in_force",
        issuing_body="European Commission (FSD)",
        language="en",
        source_url=URL,
        metadata={"source": "eu-fsd", "count": len(entities)},
    )
    version = DocumentVersion(
        document_id=doc.id,
        version_no=1,
        version_label="EU FSD snapshot",
        source_ref="csvFullSanctionsList_1_1",
        is_current=1,
    )
    secs = [
        DocumentSection(
            document_id=doc.id,
            version_id=version.id,
            section_no=e["eu_ref"] or e["logical_id"],
            heading=e["name"],
            body=e["body"],
            section_type="sanction_entry",
            level_path=None,
            anchors={"eu_ref": e["eu_ref"], "logical_id": e["logical_id"],
                     "programme": e["programme"]},
        )
        for e in entities
    ]
    return DocumentRecord(doc=doc, version=version, sections=secs)
