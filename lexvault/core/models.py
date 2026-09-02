"""lexvault 统一数据模型（适配器输出协议）。

所有数据源适配器必须输出本模块定义的结构，入库层无脑按协议写入。
源差异（分页、字段命名、正文格式）全部隔离在适配器内部。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def new_id() -> str:
    return str(uuid.uuid4())


def to_json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# 法规类型 / 状态 枚举（跨法域统一口径，各国源映射后归入）
# ---------------------------------------------------------------------------
DOC_TYPES = {
    "law",                # 法律（全国人大及其常委会）
    "admin_reg",          # 行政法规（国务院）
    "rule",               # 部门规章 / 地方政府规章
    "judicial_interpretation",  # 司法解释
    "treaty",             # 条约
    "code",               # 法典 / 汇编（US Code、CFR 等）
    "regulation",         # 欧盟条例 Regulation
    "directive",          # 欧盟指令 Directive
    "decision",           # 欧盟决定 / 一般决定
    "constitution",       # 宪法
    "other",
}

DOC_STATUS = {
    "in_force",   # 现行有效
    "repealed",   # 已废止
    "superseded", # 已被新法取代
    "expired",    # 已失效
    "draft",      # 草案 / 未生效
}


@dataclass
class LegalDocument:
    """一部法规的元数据（对应 legal_documents 表）。"""
    jurisdiction_id: str
    doc_key: str                       # 源内稳定标识（bbbs / CELEX / title+part）
    title: str
    original_title: Optional[str] = None
    doc_type: str = "other"
    status: str = "in_force"
    issuing_body: Optional[str] = None
    publish_date: Optional[str] = None # ISO YYYY-MM-DD
    effective_date: Optional[str] = None
    repeal_date: Optional[str] = None
    language: str = "zh"
    source_url: Optional[str] = None
    metadata: dict = field(default_factory=dict)  # 法域独有属性，进 metadata_json
    id: Optional[str] = None

    def __post_init__(self):
        if self.id is None:
            self.id = new_id()
        if self.doc_type not in DOC_TYPES:
            self.doc_type = "other"
        if self.status not in DOC_STATUS:
            self.status = "in_force"


@dataclass
class DocumentVersion:
    """一个版本（对应 document_versions 表）。"""
    document_id: str
    version_no: int = 1
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    version_label: Optional[str] = None
    source_ref: Optional[str] = None
    is_current: int = 1
    id: Optional[str] = None

    def __post_init__(self):
        if self.id is None:
            self.id = new_id()


@dataclass
class DocumentSection:
    """一条/一款/一项（对应 document_sections 表）。"""
    document_id: str
    version_id: str
    section_no: str
    body: str
    section_type: str = "article"
    level_path: Optional[str] = None
    heading: Optional[str] = None
    anchors: Optional[dict] = None       # 源锚点 ID，回链官网
    id: Optional[str] = None

    def __post_init__(self):
        if self.id is None:
            self.id = new_id()


@dataclass
class DocumentRecord:
    """适配器产出一部完整法规（元数据 + 版本 + 条文）。"""
    doc: LegalDocument
    version: DocumentVersion
    sections: list = field(default_factory=list)  # list[DocumentSection]


@dataclass
class AdapterResult:
    """适配器一次运行的输出。"""
    jurisdiction_code: str
    run_id: str
    records: list = field(default_factory=list)   # list[DocumentRecord]
    cursor_after: Optional[str] = None            # 断点游标
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def record_to_dict(record: DocumentRecord) -> dict:
    return {
        "doc": asdict(record.doc),
        "version": asdict(record.version),
        "sections": [asdict(s) for s in record.sections],
    }
