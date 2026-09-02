"""OFD 阅读器封装：从 flkofd.npc.gov.cn reader 服务逐页提取正文。

官方 detail2 页面的正文通过 OFD reader 渲染：
- ofdGenerateLink?filePath=prod/YYYYMMDD/xxx.ofd  -> 内部文件地址（内网 IP，公网可访问）
- reader/info?file=<内部地址>                     -> 总页数、页尺寸
- reader/text?_i=<页码>                           -> 该页所有字符（坐标定位）

本模块把逐页字符按行/列重组为纯文本，供拆条使用。
注意：reader 服务无鉴权、签名参数为固定 app_id，属 best-effort 途径，
失败时调用方应降级（只存元数据+目录树，正文留空待增量重试）。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import List, Optional

READER_BASE = "https://flkofd.npc.gov.cn"
APP_ID = "2396972e52c766e99770629cabe45e74"
TS = 1788369355048  # 页面固定时间戳参数（服务端未校验）
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


class OFDReaderError(Exception):
    pass


def _get_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # urllib.error.URLError / HTTPError / json.JSONDecodeError
        raise OFDReaderError(f"reader request failed: {e!s}") from e


def build_internal_file_url(file_path: str) -> str:
    """构造 reader 内部 file 参数：内网地址 + ofdGenerateLink 查询。"""
    base = "http://172.16.220.27:38080/law-search/amazonFile/ofdGenerateLink"
    return f"{base}?filePath={file_path}"


def fetch_total_pages(file_path: str) -> int:
    """返回 OFD 总页数。"""
    internal = build_internal_file_url(file_path)
    params = {
        "file": internal,
        "_wr_timestamp": TS,
        "_wr_app_id": APP_ID,
        "_b": "3.2.0",
        "_": TS,
        "_v": "-1",
    }
    url = f"{READER_BASE}/reader/info?{urllib.parse.urlencode(params)}"
    info = _get_json(url)
    area = info.get("area") or []
    if not area:
        raise OFDReaderError(f"reader info returned no pages: {info.get('DocID')}")
    return len(area)


def fetch_page_text(file_path: str, page: int) -> List[str]:
    """返回第 page 页的文本行（按字符 x 坐标排序重组，过滤空行）。"""
    internal = build_internal_file_url(file_path)
    params = {
        "file": internal,
        "_wr_timestamp": TS,
        "_wr_app_id": APP_ID,
        "_b": "3.2.0",
        "_": TS,
        "_v": "1",
        "_i": page,
    }
    url = f"{READER_BASE}/reader/text?{urllib.parse.urlencode(params)}"
    data = _get_json(url)
    lines: List[str] = []
    for area in data.get("areas") or []:
        for ln in area.get("lines") or []:
            chars = sorted(ln.get("chars") or [], key=lambda c: c.get("boundary", [0])[0])
            text = "".join(c.get("char", "") for c in chars).strip()
            if text:
                lines.append(text)
    return lines


def fetch_full_text(file_path: str, max_pages: Optional[int] = None,
                    pause: float = 0.3) -> str:
    """拉取 OFD 全文，返回按行拼接的纯文本。

    max_pages 限制页数（测试/调试用）；pause 为页间间隔，避免压垮服务。
    """
    total = fetch_total_pages(file_path)
    if max_pages:
        total = min(total, max_pages)
    chunks: List[str] = []
    for page in range(1, total + 1):
        try:
            lines = fetch_page_text(file_path, page)
            chunks.append("\n".join(lines))
        except OFDReaderError as e:
            # 单页失败不中断：正文可能缺页，返回已有内容并附带提示
            chunks.append(f"\n[reader page {page} failed: {e}]")
        time.sleep(pause)
    return "\n".join(chunks)
