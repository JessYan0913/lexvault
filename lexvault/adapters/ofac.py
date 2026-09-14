"""OFAC 制裁名单适配器（美国财政部海外资产控制办公室）。

数据源：Sanctions List Service (SLS) 导出 CSV
    https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_PRIM.CSV
    https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_ALT.CSV
    https://www.treasury.gov/ofac/downloads/add.csv  （SDN 地址/国别文件）

设计：整个合并制裁名单作为 1 部文档（doc_key=ofac-consolidated），
每个制裁实体 = 1 条 section：
    section_no  = ent_num（实体编号）
    heading     = 主名称（primary name）
    body        = 类型 | 项目 | 国别 | 地址 | 别名 | 备注 等可检索文本
    anchors     = 结构化字段（ent_num / countries / addresses / program / type）
这样 MCP 的 search_local 可直接按名称/别名/项目/国别检索命中实体。

注意：SDN 实体的地址/国别来自 treasury.gov 下载中心的 add.csv
（SLS 的 CONS_ADD.CSV 只覆盖 Non-SDN 补充实体，不完整）。
"""
from __future__ import annotations

import csv
import io
import urllib.request
from typing import Optional

from lexvault.core.models import (
    DocumentRecord,
    DocumentSection,
    DocumentVersion,
    LegalDocument,
    new_id,
)

BASE = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# CONS_PRIM 12 列（无表头）：ent_num, primary_name, type, program, title,
# call_sign, vess_type, tonnage, grt, vess_flag, vess_owner, remarks
_PRIM_COLS = ["ent_num", "name", "type", "program", "title", "call_sign",
              "vess_type", "tonnage", "grt", "vess_flag", "vess_owner", "remarks"]
# CONS_ALT 实际为 5 列（含末尾 remarks）：ent_num, alt_type, alt_name, alt_remarks, remarks
_ALT_COLS = ["ent_num", "alt_type", "alt_name", "alt_remarks", "remarks"]
# add.csv 无表头 6 列：ent_num, add_num, addr1, addr2, country, state
_ADD_COLS = ["ent_num", "add_num", "addr1", "addr2", "country", "state"]

# SDN 地址/国别文件走 OFAC 下载中心（SLS 的 CONS_ADD.CSV 仅覆盖 Non-SDN 补充）
ADD_URL = "https://www.treasury.gov/ofac/downloads/add.csv"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _read_csv(raw: bytes, cols: list[str]) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = []
    for line in reader:
        if not line:
            continue
        # 行内列数不足则补齐，避免解包错误
        line = (line + [""] * (len(cols) - len(line)))[: len(cols)]
        rows.append(dict(zip(cols, line)))
    return rows


def _norm(v: str) -> str:
    v = (v or "").strip()
    return "" if v in ("-0-", "-0- ", "") else v


class OFACAdapter:
    """OFAC 合并制裁名单适配器：拉取 → 组装 DocumentRecord 列表。"""

    def fetch_primary(self) -> list[dict]:
        """拉取主名单：SDN.CSV（完整 19326 实体）+ CONS_PRIM.CSV（Non-SDN 补充）。"""
        sdn_raw = _get(f"{BASE}/SDN.CSV")
        sdn = _read_csv(sdn_raw, _PRIM_COLS)
        cons_raw = _get(f"{BASE}/CONS_PRIM.CSV")
        cons = _read_csv(cons_raw, _PRIM_COLS)
        # 合并去重（按 ent_num）
        merged: dict[str, dict] = {}
        for r in sdn + cons:
            ent = (r.get("ent_num") or "").strip()
            if ent:
                merged.setdefault(ent, r)
        return list(merged.values())

    def fetch_alt(self) -> list[dict]:
        raw = _get(f"{BASE}/CONS_ALT.CSV")
        return _read_csv(raw, _ALT_COLS)

    def fetch_add(self) -> list[dict]:
        """拉取 SDN 地址/国别文件（treasury.gov 下载中心 add.csv）。"""
        raw = _get(ADD_URL)
        rows = _read_csv(raw, _ADD_COLS)
        # 过滤掉占位行与空 ent_num
        out = []
        for r in rows:
            ent = (r.get("ent_num") or "").strip()
            if ent:
                out.append(r)
        return out

    @staticmethod
    def build_records(jurisdiction_id: str, prim: list[dict],
                      alt: list[dict], add: list[dict] | None = None) -> DocumentRecord:
        """把主名 + 别名 + 地址/国别合并成 1 部文档记录。

        add 为 SDN 地址文件行（列：ent_num/add_num/addr1/addr2/country/state），
        可选；传入时会给每条实体补充国别与地址，写进 body（可检索）与
        anchors（结构化字段）。
        """
        # 按 ent_num 收集别名（真正的别名文本在 alt_remarks；alt_name 通常是 aka）
        alts_by_ent: dict[str, list[str]] = {}
        for a in alt:
            ent = (a.get("ent_num") or "").strip()
            an = _norm(a.get("alt_remarks") or "")
            if ent and an and an != "-0-":
                alts_by_ent.setdefault(ent, []).append(an)

        # 按 ent_num 收集地址/国别
        add_by_ent: dict[str, list[dict]] = {}
        for a in add or []:
            ent = (a.get("ent_num") or "").strip()
            if ent:
                add_by_ent.setdefault(ent, []).append(a)

        doc = LegalDocument(
            jurisdiction_id=jurisdiction_id,
            doc_key="ofac-consolidated",
            title="OFAC Consolidated Sanctions List（美国合并制裁名单）",
            original_title="Consolidated Sanctions List (Non-SDN & SDN)",
            doc_type="other",
            status="in_force",
            issuing_body="US Department of the Treasury, OFAC",
            language="en",
            source_url="https://ofac.treasury.gov/sanctions-list-service",
            metadata={"source": "sanctions-list-service", "count": len(prim)},
        )
        version = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            version_label="CONS_PRIM snapshot",
            source_ref="CONS_PRIM.CSV + CONS_ALT.CSV + add.csv",
            is_current=1,
        )
        sections: list[DocumentSection] = []
        for r in prim:
            ent = (r.get("ent_num") or "").strip()
            name = _norm(r.get("name") or "")
            if not name:
                continue
            typ = _norm(r.get("type") or "")
            prog = _norm(r.get("program") or "")
            remarks = _norm(r.get("remarks") or "")
            alts = alts_by_ent.get(ent, [])
            alt_names = "；".join(dict.fromkeys(alts))

            # 地址/国别聚合
            addr_rows = add_by_ent.get(ent, [])
            countries: list[str] = []
            addr_strs: list[str] = []
            for ar in addr_rows:
                country = _norm(ar.get("country") or "")
                if country and country != "-0-" and country not in countries:
                    countries.append(country)
                addr1 = _norm(ar.get("addr1") or "")
                addr2 = _norm(ar.get("addr2") or "")
                state = _norm(ar.get("state") or "")
                if addr1 and addr1 != "-0-":
                    addr_strs.append(addr1)
                if addr2 and addr2 != "-0-":
                    addr_strs.append(addr2)
                if state and state != "-0-":
                    addr_strs.append(state)

            parts = []
            if typ:
                parts.append(f"类型: {typ}")
            if prog:
                parts.append(f"项目: {prog}")
            if countries:
                _countries = ",".join(dict.fromkeys(countries))
                parts.append(f"国别: {_countries}")
            if addr_strs:
                _addrs = ";".join(dict.fromkeys(addr_strs))
                parts.append(f"地址: {_addrs}")
            if alt_names:
                parts.append(f"别名: {alt_names}")
            if remarks:
                parts.append(f"备注: {remarks}")
            body = " | ".join(parts) if parts else name

            sections.append(DocumentSection(
                document_id=doc.id,
                version_id=version.id,
                section_no=ent or f"s{i}",
                heading=name,
                body=body,
                section_type="sanction_entry",
                level_path=None,
                anchors={
                    "ent_num": ent,
                    "type": typ,
                    "program": prog,
                    "countries": list(dict.fromkeys(countries)),
                    "addresses": list(dict.fromkeys(addr_strs)),
                },
            ))

        return DocumentRecord(doc=doc, version=version, sections=sections)
