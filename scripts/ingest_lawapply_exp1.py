#!/usr/bin/env python3
"""入库：最高人民法院关于适用《涉外民事关系法律适用法》若干问题的解释（一）
（法释〔2012〕24号，2020修正版，19条）—— 正文来自最高法公报官方页面。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lexvault.core.models import (  # noqa: E402
    DocumentRecord, DocumentSection, DocumentVersion, LegalDocument,
)
from lexvault.core.store import Store  # noqa: E402

# 官方正文（web_fetch 抓取，段落以"第X条"开头）
TEXT = """为正确审理涉外民事案件，根据《中华人民共和国涉外民事关系法律适用法》的规定，对人民法院适用该法的有关问题解释如下：
第一条 民事关系具有下列情形之一的，人民法院可以认定为涉外民事关系：（一）当事人一方或双方是外国公民、外国法人或者其他组织、无国籍人；（二）当事人一方或双方的经常居所地在中华人民共和国领域外；（三）标的物在中华人民共和国领域外；（四）产生、变更或者消灭民事关系的法律事实发生在中华人民共和国领域外；（五）可以认定为涉外民事关系的其他情形。
第二条 涉外民事关系法律适用法实施以前发生的涉外民事关系，人民法院应当根据该涉外民事关系发生时的有关法律规定确定应当适用的法律；当时法律没有规定的，可以参照涉外民事关系法律适用法的规定确定。
第三条 涉外民事关系法律适用法与其他法律对同一涉外民事关系法律适用规定不一致的，适用涉外民事关系法律适用法的规定，但《中华人民共和国票据法》《中华人民共和国海商法》《中华人民共和国民用航空法》等商事领域法律的特别规定以及知识产权领域法律的特别规定除外。涉外民事关系法律适用法对涉外民事关系的法律适用没有规定而其他法律有规定的，适用其他法律的规定。
第四条 中华人民共和国法律没有明确规定当事人可以选择涉外民事关系适用的法律，当事人选择适用法律的，人民法院应认定该选择无效。
第五条 一方当事人以双方协议选择的法律与系争的涉外民事关系没有实际联系为由主张选择无效的，人民法院不予支持。
第六条 当事人在一审法庭辩论终结前协议选择或者变更选择适用的法律的，人民法院应予准许。各方当事人援引相同国家的法律且未提出法律适用异议的，人民法院可以认定当事人已经就涉外民事关系适用的法律做出了选择。
第七条 当事人在合同中援引尚未对中华人民共和国生效的国际条约的，人民法院可以根据该国际条约的内容确定当事人之间的权利义务，但违反中华人民共和国社会公共利益或中华人民共和国法律、行政法规强制性规定的除外。
第八条 有下列情形之一，涉及中华人民共和国社会公共利益、当事人不能通过约定排除适用、无需通过冲突规范指引而直接适用于涉外民事关系的法律、行政法规的规定，人民法院应当认定为涉外民事关系法律适用法第四条规定的强制性规定：（一）涉及劳动者权益保护的；（二）涉及食品或公共卫生安全的；（三）涉及环境安全的；（四）涉及外汇管制等金融安全的；（五）涉及反垄断、反倾销的；（六）应当认定为强制性规定的其他情形。
第九条 一方当事人故意制造涉外民事关系的连结点，规避中华人民共和国法律、行政法规的强制性规定的，人民法院应认定为不发生适用外国法律的效力。
第十条 涉外民事争议的解决须以另一涉外民事关系的确认为前提时，人民法院应当根据该先决问题自身的性质确定其应当适用的法律。
第十一条 案件涉及两个或者两个以上的涉外民事关系时，人民法院应当分别确定应当适用的法律。
第十二条 当事人没有选择涉外仲裁协议适用的法律，也没有约定仲裁机构或者仲裁地，或者约定不明的，人民法院可以适用中华人民共和国法律认定该仲裁协议的效力。
第十三条 自然人在涉外民事关系产生或者变更、终止时已经连续居住一年以上且作为其生活中心的地方，人民法院可以认定为涉外民事关系法律适用法规定的自然人的经常居所地，但就医、劳务派遣、公务等情形除外。
第十四条 人民法院应当将法人的设立登记地认定为涉外民事关系法律适用法规定的法人的登记地。
第十五条 人民法院通过由当事人提供、已对中华人民共和国生效的国际条约规定的途径、中外法律专家提供等合理途径仍不能获得外国法律的，可以认定为不能查明外国法律。根据涉外民事关系法律适用法第十条第一款的规定，当事人应当提供外国法律，其在人民法院指定的合理期限内无正当理由未提供该外国法律的，可以认定为不能查明外国法律。
第十六条 人民法院应当听取各方当事人对应当适用的外国法律的内容及其理解与适用的意见，当事人对该外国法律的内容及其理解与适用均无异议的，人民法院可以予以确认；当事人有异议的，由人民法院审查认定。
第十七条 涉及香港特别行政区、澳门特别行政区的民事关系的法律适用问题，参照适用本规定。
第十八条 涉外民事关系法律适用法施行后发生的涉外民事纠纷案件，本解释施行后尚未终审的，适用本解释；本解释施行前已经终审，当事人申请再审或者按照审判监督程序决定再审的，不适用本解释。
第十九条 本院以前发布的司法解释与本解释不一致的，以本解释为准。"""


def main() -> int:
    store = Store(os.path.join(ROOT, "db", "lexvault.db"))
    store.connect(); store.ensure_schema()
    jid = store.upsert_jurisdiction("cn", "中国（全国人大）", "api",
                                    "https://flk.npc.gov.cn", "npc", "{}")

    # 按"第X条"切分
    pat = re.compile(r"第[一二三四五六七八九十百零]+条")
    matches = list(pat.finditer(TEXT))
    arts = []
    for i, m in enumerate(matches):
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(TEXT)
        body = TEXT[m.end():body_end].strip()
        arts.append((m.group(), body))

    doc = LegalDocument(
        jurisdiction_id=jid,
        doc_key="sj-apply-law-use-2012-1",
        title="最高人民法院关于适用《中华人民共和国涉外民事关系法律适用法》若干问题的解释（一）",
        original_title="法释〔2012〕24号",
        doc_type="judicial_interpretation",
        status="in_force",
        issuing_body="最高人民法院",
        language="zh",
        source_url="http://gongbao.court.gov.cn/Details/cd2e8428c4de894455038e760c6472.html",
        metadata={"type": "judicial_interpretation", "year": 2012, "revised": 2020},
    )
    ver = DocumentVersion(document_id=doc.id, version_no=1,
                          version_label="2020修正版", source_ref="最高法公报", is_current=1)
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
