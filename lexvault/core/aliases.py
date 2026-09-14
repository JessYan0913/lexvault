"""跨语言检索增强：转写表、术语对照、查询扩展。

为 search_local 提供三类跨语言桥接：
1. 西里尔→拉丁转写（BGN 标准，俄语制裁实体常用）
2. 中英制裁实体译名表（中文名查英文制裁名单）
3. 英文法律术语→中文对照（英文概念查中文法条）
"""
from __future__ import annotations

# ---------- 1. 西里尔→拉丁转写（BGN 标准，常用映射） ----------
CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate_cyrillic(text: str) -> str:
    """把西里尔字母转写为拉丁。仅转写西里尔字符，其余原样保留。"""
    out = []
    for ch in text:
        low = ch.lower()
        if low in CYRILLIC_TO_LATIN:
            mapped = CYRILLIC_TO_LATIN[low]
            out.append(mapped if ch.islower() else mapped.upper())
        else:
            out.append(ch)
    return "".join(out)


# ---------- 2. 中英制裁实体译名表（中文名 → 英文标准名） ----------
ENTITY_ALIASES = {
    # 俄罗斯
    "俄罗斯国防出口": "Rosoboronexport",
    "俄罗斯国防出口公司": "Rosoboronexport",
    "俄罗斯技术集团": "Rostec",
    "俄罗斯石油": "Rosneft",
    "俄罗斯天然气工业": "Gazprom",
    "俄气": "Gazprom",
    "俄罗斯联邦储蓄银行": "Sberbank",
    "俄罗斯外贸银行": "VTB",
    "阿尔法银行": "Alfa Bank",
    "瓦格纳": "Wagner",
    # 中东
    "塔利班": "TALIBAN",
    "萨达姆侯赛因": "Saddam Hussein",
    "萨达姆·侯赛因": "Saddam Hussein",
    "本拉登": "Bin Laden",
    "本·拉登": "Bin Laden",
    "基地组织": "Al Qaeda",
    "真主党": "Hezbollah",
    "哈马斯": "Hamas",
    "胡塞": "Houthi",
    # 古巴/其他
    "古巴国家银行": "Banco Nacional de Cuba",
    "朝鲜": "DPRK",
    "伊朗伊斯兰革命卫队": "IRGC",
    "伊斯兰革命卫队": "IRGC",
}

# ---------- 3. 英文法律术语 → 中文对照（英文概念查中文法条） ----------
LEGAL_TERMS = {
    "arbitration agreement": "仲裁协议",
    "arbitral award": "仲裁裁决",
    "recognition and enforcement of foreign arbitral awards": "承认及执行外国仲裁裁决",
    "foreign arbitral award": "外国仲裁裁决",
    "applicable law": "准据法",
    "governing law": "准据法",
    "choice of law": "法律适用",
    "force majeure": "不可抗力",
    "liquidated damages": "违约金",
    "confidentiality": "保密",
    "jurisdiction clause": "管辖权",
    "exclusive jurisdiction": "专属管辖",
    "interim measure": "临时措施",
    "preservation of evidence": "证据保全",
    "sanction": "制裁",
    "export control": "出口管制",
    "economic sanction": "经济制裁",
    "asset freeze": "资产冻结",
    "anti-money laundering": "反洗钱",
    "bribery": "贿赂",
    "corruption": "贪污",
    "foreign corrupt practices": "对外贿赂",
    "due diligence": "尽职调查",
    "contractual obligation": "合同义务",
    "breach of contract": "违约",
    "termination of contract": "合同解除",
    "unconscionable": "显失公平",
    "public policy": "公共政策",
    "public interest": "社会公共利益",
    "service of process": "送达",
    "service of documents": "送达",
    "judicial assistance": "司法协助",
    "letters rogatory": "司法协助请求",
    "evidence": "证据",
    "witness": "证人",
    "appeal": "上诉",
    "res judicata": "既判力",
    "limitation period": "诉讼时效",
    "statute of limitations": "诉讼时效",
    "sovereign immunity": "主权豁免",
    "state immunity": "国家豁免",
    "tort": "侵权",
    "intellectual property": "知识产权",
    "patent": "专利",
    "trademark": "商标",
    "copyright": "著作权",
    "trade secret": "商业秘密",
    "insurance": "保险",
    "freight": "运费",
    "bill of lading": "提单",
    "letter of credit": "信用证",
    "incoterms": "国际贸易术语",
    "antitrust": "反垄断",
    "competition law": "反垄断",
    "data protection": "个人信息保护",
    "personal information": "个人信息",
    "cross-border": "跨境",
    "foreign investment": "外商投资",
    "bilateral investment treaty": "双边投资协定",
}


def expand_query(query: str) -> list[str]:
    """查询扩展：返回 [原查询, 扩展词...]（去重、不包含空串）。

    规则：
    - 含中文 → 查实体译名表（中文→英文）
    - 英文 → 查术语表（英文→中文）
    - 含西里尔 → 转写为拉丁
    """
    results = [query]
    q_lower = query.lower().strip()

    # 中文 → 英文实体名
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in query)
    if has_cjk:
        for cn, en in ENTITY_ALIASES.items():
            if cn in query:
                results.append(en)

    # 英文 → 中文术语
    else:
        for en, cn in LEGAL_TERMS.items():
            if en in q_lower or q_lower in en:
                results.append(cn)

    # 西里尔 → 拉丁
    if any("\u0400" <= ch <= "\u04ff" for ch in query):
        results.append(transliterate_cyrillic(query))

    # 去重保序
    seen = set()
    out = []
    for r in results:
        r = r.strip()
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


# ---------- 4. 规范化增强（供筛查/检索共用） ----------
import re as _re
import unicodedata as _ud

# 公司后缀符号化表：不同语言的"公司/集团/有限/控股"统一为 CO_TOKEN
CO_TOKEN = "_CO_"
CORPORATE_SUFFIXES = {
    "llc", "ltd", "limited", "inc", "incorporated", "corp", "corporation",
    "co", "company", "gmbh", "ag", "kg", "sa", "sarl", "srl", "spa",
    "oao", "ooo", "zao", "jsc", "pjsc", "plc", "holding", "holdings",
    "group", "bhd", "pte", "pvt", "pty", "bv", "nv", "kk", "yk", "gm",
}


def fold_diacritics(text: str) -> str:
    """变音符号折叠：é→e、ü→u、ø→o 等（NFKD 分解后去掉组合音标）。"""
    if not text:
        return text
    nfkd = _ud.normalize("NFKD", text)
    return "".join(c for c in nfkd if not _ud.combining(c))


def collapse_corporate_suffixes(text: str) -> str:
    """把公司后缀 token 统一为 CO_TOKEN（LLC=Limited=GmbH=OAO）。"""
    if not text:
        return text
    tokens = text.split()
    out = []
    for t in tokens:
        key = t.lower().strip(".")
        out.append(CO_TOKEN if key in CORPORATE_SUFFIXES else t)
    return " ".join(out)


def normalize_enhanced(text: str) -> str:
    """增强归一化：折叠变音 → 大写 → 去标点 → 公司后缀符号化 → 压缩空白。

    - Rosoboroneksport OAO → ROSOBORONEKSPORT _CO_
    - TALIBAN GmbH ↔ TALIBAN LTD 可精确匹配
    """
    if not text:
        return ""
    n = fold_diacritics(text).upper()
    n = _re.sub(r"[^A-Z0-9\u4e00-\u9fff ]", " ", n)
    n = _re.sub(r"\s+", " ", n).strip()
    n = collapse_corporate_suffixes(n)
    return n


def token_set_similar(a: str, b: str) -> float:
    """词序无关相似度：比较两边 token 集合（Mao Zedong ↔ Zedong Mao）。

    返回 0~1：两集合共有 token 占并集的比例。
    """
    if not a or not b:
        return 0.0
    ta = {t for t in a.split() if t and t != CO_TOKEN}
    tb = {t for t in b.split() if t and t != CO_TOKEN}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
