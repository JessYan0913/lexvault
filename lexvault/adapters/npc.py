"""中国全国人大法律法规数据库（flk.npc.gov.cn）采集适配器。

数据链路（均已实测验证）：
  1. 搜索列表：POST /law-search/search/list
     body: {"searchRange":1,"searchContent":关键词,"pageNum":N,"pageSize":S,"searchType":1,...}
     返回 rows[]，关键字段：bbbs(法规唯一标识), title(带高亮), flxz(类型), gbrq(公布日期),
     sxrq(生效日期), sxx(效力级别码), zdjgName(公布机关), zdjgCodeId, flfgCodeId
  2. 详情：GET /law-search/search/flfgDetails?bbbs=xxx
     返回 data{bbbs,title,flxz,gbrq,sxrq,sxx,zdjgName,ossFile{ossWordPath,...},
     content(条目树，只含条目标题不含正文), lsyg(历史沿革), xgzl(相关文件)}
  3. 正文下载：POST /law-search/download/batch  [{"bbbs":...,"format":"docx"}]
     返回 data[0].url（OBS 签名 URL，约 1 小时有效），下载即得 .docx
  4. 拆条：解析 docx 正文，按“第X条”切分为条文

参考 flfgCodeId 映射（法规类型）：
  101 宪法 102 宪法修正案 110 基本法律 120 法律 130 法律解释
  140 有关法律问题和重大问题的决定 150 行政法规 155 监察法规
  160 监察司法解释 170 司法解释 180 司法解释性质文件 190 军事法规
  195 军事规章 200 部门规章
效力级别 sxx：1 法律 2 行政法规 3 监察法规 4 司法解释 5 地方性法规 ...
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from ..core.models import (
    AdapterResult, DocumentRecord, DocumentSection, DocumentVersion, LegalDocument,
)

BASE = "https://flk.npc.gov.cn"
SEARCH_URL = BASE + "/law-search/search/list"
DETAIL_URL = BASE + "/law-search/search/flfgDetails"
DOWNLOAD_URL = BASE + "/law-search/download/batch"

# 类型码 -> 统一 doc_type
FJ_TYPE_MAP = {
    100: "constitution", 101: "constitution", 102: "constitution", 110: "law", 120: "law",
    130: "law", 140: "law", 150: "law", 155: "law", 160: "law", 170: "law",
    180: "judicial_interpretation", 190: "decision", 195: "law", 200: "decision",
    201: "admin_reg", 210: "admin_reg", 215: "decision",
    220: "admin_reg",
    320: "judicial_interpretation", 330: "judicial_interpretation", 340: "judicial_interpretation", 350: "decision",
}
# 效力级别码 sxx -> 状态映射（1 现行有效为主，3 已修改，其余按需）
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": BASE + "/",
    "Origin": BASE,
    "Content-Type": "application/json;charset=utf-8",
    "Accept": "application/json, text/plain, */*",
}

# 中文数字
_CN_NUM = "零一二三四五六七八九十百千"
_CN_NUM_RE = re.compile(rf"^第([{_CN_NUM}十百千]+)条(?=[\u3000\s]|$)")
# 结构标题（编/章/节/附则/目录），不构成条文
_STRUCT_RE = re.compile(r"^(第[一二三四五六七八九十百千]+(编|章|节)[\u3000\s]|附则|总则|目[\u3000\s]*录)")


def _http_json(url: str, method: str = "GET", body: Optional[dict] = None,
               headers: Optional[dict] = None, timeout: int = 30) -> dict:
    """发送 JSON 请求，带 WAF cookie 会话与 307 自动重试。

    flk.npc.gov.cn 由 WZWS WAF 保护：首次 POST 会返回 307 + Set-Cookie(wzws_cid)，
    需先建立 cookie 会话再重试。此处用 CookieJar 自动处理。
    """
    hd = dict(DEFAULT_HEADERS)
    if headers:
        hd.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, headers=hd, method=method)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    # 防 BOM / 编码
    text = raw.decode("utf-8-sig")
    return json.loads(text)


# WAF cookie 会话：首次 POST 返回 307 + Set-Cookie(wzws_cid)，
# 不自动跟随重定向，直接从响应头抓 cookie 再重试。
_wzws_cookie: Optional[str] = None


class _NoRedirect(HTTPRedirectHandler):
    """不自动跟随重定向，让 HTTPError 暴露 307/302 与 Set-Cookie 头。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_json_waf(url: str, method: str = "GET", body: Optional[dict] = None,
                   headers: Optional[dict] = None, timeout: int = 30,
                   retries: int = 3) -> dict:
    """带 WAF cookie 的 JSON 请求：307/302 时抓 Set-Cookie(wzws_cid) 并重试。"""
    global _wzws_cookie
    opener = build_opener(_NoRedirect)
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        hd = dict(DEFAULT_HEADERS)
        if headers:
            hd.update(headers)
        if _wzws_cookie:
            hd["Cookie"] = _wzws_cookie
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=data, headers=hd, method=method)
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
            text = raw.decode("utf-8-sig")
            return json.loads(text)
        except HTTPError as e:
            last_err = e
            if e.code in (302, 307):
                # 从跳转响应头抓 wzws_cid
                for k, v in e.headers.items():
                    if k.lower() == "set-cookie" and "wzws_cid" in v:
                        _wzws_cookie = v.split(";", 1)[0]
                        break
                time.sleep(0.8 * (attempt + 1))  # 退避后再重试，避免打爆 WAF
                continue
            raise
        except (TimeoutError, OSError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))  # 网络超时/断连：退避重试
    raise RuntimeError(f"请求失败（重试 {retries} 次）: {last_err}")


def strip_highlight(title: str) -> str:
    """去掉搜索结果里的 <em class='highlight'> 高亮标签。"""
    return re.sub(r"</?em[^>]*>", "", title)


def _download(url: str, timeout: int = 60, max_retries: int = 2) -> bytes:
    """下载文件；校验非空且为 zip（docx 魔数），失败重试。"""
    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            req = Request(url, headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]})
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            if not raw:
                raise RuntimeError("下载内容为空")
            # 接受 docx(PK) 与老式 .doc(OLE2 D0CF11E0)，其他视为异常
            if raw[:2] != b"PK" and raw[:4] != b"\xd0\xcf\x11\xe0":
                raise RuntimeError(f"下载内容不是可识别文档 (magic={raw[:8]!r})")
            return raw
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"下载失败（重试 {max_retries} 次）: {last_err}")


# ---------------------------------------------------------------------------
# docx 解析（零依赖：zipfile + XML）
# ---------------------------------------------------------------------------
def _parse_docx_text(raw: bytes) -> str:
    """从 docx 提取纯文本（按段落换行）。

    优先 zipfile + ElementTree 零依赖解析；遇到非标准/损坏结构时
    回退到 macOS textutil（或 Python zipfile 仅读 document.xml 失败时）。
    """
    import zipfile
    from xml.etree import ElementTree as ET
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(raw)) as z:
            xml = z.read("word/document.xml")
        root = ET.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paras = []
        for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            texts = [
                t.text or ""
                for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
            ]
            paras.append("".join(texts))
        return "\n".join(paras)
    except Exception:
        return _parse_docx_text_textutil(raw)


def _parse_docx_text_textutil(raw: bytes) -> str:
    """回退：写入临时文件，用 macOS 自带 textutil 转纯文本。"""
    import subprocess
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as f:
        f.write(raw)
        path = f.name
    try:
        out = subprocess.run(
            ["/usr/bin/textutil", "-convert", "txt", "-stdout", path],
            capture_output=True, timeout=60,
        )
        if out.returncode == 0:
            return out.stdout.decode("utf-8", errors="replace")
        return ""
    except Exception:
        return ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 拆条
# ---------------------------------------------------------------------------
def split_sections(text: str) -> List[Tuple[str, str]]:
    """把整文文本按‘第X条’切成 [(条号, 内容)]，忽略编/章/节等结构标题。

    docx 中条文是“第X条　正文”同行格式，正文与条号同段；
    也可能遇到“第一条至第三条”等合并标题、或“附则”等尾部小节。
    返回 (section_no, body)。
    """
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    sections: List[Tuple[str, str]] = []
    cur_no: Optional[str] = None
    cur_buf: List[str] = []

    def flush():
        if cur_no is not None and cur_buf:
            sections.append((cur_no, "\n".join(cur_buf)))

    for ln in lines:
        m = _CN_NUM_RE.match(ln)
        if m:
            flush()
            cur_no = "第" + m.group(1) + "条"
            rest = ln[m.end():].lstrip("\u3000 \t")
            cur_buf = [rest] if rest else []
            continue
        # 结构标题（编/章/节/附则）不入条文，但作为分界：若已有未完成条文则先收尾
        if _STRUCT_RE.match(ln):
            flush()
            cur_no = None
            cur_buf = []
            continue
        if cur_no is not None:
            cur_buf.append(ln)
    flush()
    return sections


# ---------------------------------------------------------------------------
# 适配器主类
# ---------------------------------------------------------------------------
class NPCAdapter:
    """全国人大库适配器。run(keyword, ...) 产出 AdapterResult。"""

    name = "npc"
    jurisdiction_code = "cn"

    def __init__(self, session=None):
        self._session = session  # 预留（urllib 无会话，先空）

    # -- 搜索 ---------------------------------------------------------------
    def search(self, keyword: str = "", page: int = 1, page_size: int = 20,
               search_type: int = 1, search_range: int = 1,
               gbrq: Optional[list] = None) -> List[dict]:
        """search_type=1 全文; search_range=1 现行有效(不含已废止)。

        gbrq: [start, end] 按公布日期过滤（如 ["2026-08-01", "2026-09-02"]），
              增量更新用它拉取“最近公布/修改”的法规。
        """
        body = {
            "searchRange": search_range,
            "sxrq": [], "gbrq": gbrq or [], "sxx": [], "gbrqYear": [],
            "flfgCodeId": [101, 102, 110, 120, 130, 140, 150, 155, 160, 170, 180, 190, 195, 200],
            "zdjgCodeId": [],
            "searchContent": keyword,
            "pageNum": page,
            "pageSize": page_size,
            "searchType": search_type,
        }
        d = _http_json_waf(SEARCH_URL, "POST", body)
        if d.get("code") != 200:
            raise RuntimeError(f"NPC 搜索失败: {d.get('msg')}")
        return d.get("rows") or []

    # -- 详情 ---------------------------------------------------------------
    def get_detail(self, bbbs: str) -> dict:
        d = _http_json_waf(f"{DETAIL_URL}?bbbs={bbbs}")
        if d.get("code") != 200:
            raise RuntimeError(f"NPC 详情失败: {d.get('msg')}")
        return d.get("data") or {}

    # -- 下载 docx -----------------------------------------------------------
    def download_docx(self, bbbs: str, title: str = "") -> bytes:
        d = _http_json_waf(DOWNLOAD_URL, "POST", [{"bbbs": bbbs, "format": "docx"}])
        if d.get("code") != 200 or not d.get("data"):
            raise RuntimeError(f"NPC 下载链接失败: {d.get('msg')}")
        url = d["data"][0]["url"]
        return _download(url)

    # -- 转换协议 -------------------------------------------------------------
    def to_record(self, jur_id: str, row: dict, detail: Optional[dict] = None,
                  docx_raw: Optional[bytes] = None) -> DocumentRecord:
        """把一条搜索结果（可选 detail/docx）转为 DocumentRecord。"""
        bbbs = row["bbbs"]
        if detail is None:
            detail = self.get_detail(bbbs)
        title = strip_highlight(detail.get("title") or row.get("title") or "")
        flfg_code = row.get("flfgCodeId") or 0
        doc_type = FJ_TYPE_MAP.get(flfg_code, "other")
        # 注意：sxx=3 是“已修改”效力级别，不代表已废止（如民法典现行有效但 sxx=3），
        # 因此 status 一律取 in_force，废止/失效判断留给后续 lsyg 沿革解析或人工标注。
        status = "in_force"

        meta = {
            "bbbs": bbbs,
            "flfgCodeId": flfg_code,
            "sxx": row.get("sxx"),
            "zdjgCodeId": row.get("zdjgCodeId"),
            "score": row.get("score"),
        }
        # 详情里的扩展字段
        if detail.get("lsyg"):
            meta["lsyg"] = detail["lsyg"]
        if detail.get("xgzl"):
            meta["xgzl"] = detail["xgzl"]
        if detail.get("ossFile"):
            meta["ossWordPath"] = detail["ossFile"].get("ossWordPath")

        doc = LegalDocument(
            jurisdiction_id=jur_id,
            doc_key=bbbs,
            title=title,
            original_title=detail.get("title") or None,
            doc_type=doc_type,
            status=status,
            issuing_body=detail.get("zdjgName") or None,
            publish_date=detail.get("gbrq") or None,
            effective_date=detail.get("sxrq") or None,
            language="zh",
            source_url=f"{BASE}/detail2.html?{bbbs}",
            metadata=meta,
        )
        ver = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            effective_from=detail.get("sxrq") or None,
            version_label="现行文本",
            is_current=1,
        )
        sections: List[DocumentSection] = []
        if docx_raw is not None:
            text = _parse_docx_text(docx_raw)
            parsed = list(split_sections(text))
            if parsed:
                for no, body in parsed:
                    sections.append(DocumentSection(
                        document_id=doc.id, version_id=ver.id,
                        section_no=no, body=body, level_path="1",
                    ))
            else:
                # 无“第X条”结构（复函/通知/解释性文件）：整篇作为一条正文
                sections.append(DocumentSection(
                    document_id=doc.id, version_id=ver.id,
                    section_no="全文", body=text.strip(), level_path="1",
                ))
        return DocumentRecord(doc=doc, version=ver, sections=sections)

    # -- 主入口 ---------------------------------------------------------------
    def run(self, keyword: str, max_docs: int = 3, page_size: int = 20,
            fetch_body: bool = True) -> AdapterResult:
        run_id = str(uuid.uuid4())
        records: List[DocumentRecord] = []
        fetched = 0
        page = 1
        while fetched < max_docs:
            rows = self.search(keyword, page=page, page_size=page_size)
            if not rows:
                break
            for row in rows:
                if fetched >= max_docs:
                    break
                bbbs = row["bbbs"]
                # 详情
                detail = self.get_detail(bbbs)
                docx_raw = None
                if fetch_body:
                    try:
                        docx_raw = self.download_docx(bbbs, row.get("title") or "")
                    except Exception as e:
                        print(f"  [warn] 下载 {bbbs} 失败: {e}")
                rec = self.to_record("", row, detail, docx_raw)
                records.append(rec)
                fetched += 1
                time.sleep(0.4)  # 温和限速
            page += 1
        return AdapterResult(
            jurisdiction_code=self.jurisdiction_code,
            run_id=run_id,
            records=records,
            cursor_after=str(page - 1),
        )


# ---------------------------------------------------------------------------
# CLI 直接跑：python -m lexvault.adapters.npc "关键词" [max_docs]
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import os
    # 工程根目录（含 lexvault 包）
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from lexvault.core.store import Store

    keyword = sys.argv[1] if len(sys.argv) > 1 else "民法典"
    max_docs = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    store = Store(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "db", "lexvault.db"))
    store.connect()
    store.ensure_schema()
    jid = store.upsert_jurisdiction(
        code="cn", name="中国全国人大库", source_type="api",
        base_url=BASE, adapter="npc",
        config_json={"search_url": SEARCH_URL},
    )

    adapter = NPCAdapter()
    result = adapter.run(keyword, max_docs=max_docs)
    print(f"拉取 {len(result.records)} 部法规")
    sr_id = store.begin_run(jid, result.run_id)
    inserted = 0
    for rec in result.records:
        rec.doc.jurisdiction_id = jid
        doc_id, ver_id = store.save_record(rec)
        print(f"  ✅ {rec.doc.title} | 条文数 {len(rec.sections)} | {rec.doc.publish_date}")
        inserted += 1
    store.finish_run(sr_id, "success", docs_fetched=len(result.records),
                     docs_inserted=inserted, docs_updated=0,
                     cursor_after=result.cursor_after)
    store.close()
    print("入库完成")
