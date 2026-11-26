"""作者 / 单位的脏数据清洗。

脏数据来自 PDF 文本层：上标脚注被粘进名字尾部（`Liyuan Xuea∗`）、重音符号
被拆成 `´` + 元音（`Jordi Verd´u`）、以及大段正文被误判成人名
（`we demonstrate a universal inverse design approach`）。单位一侧多是
HTML 实体未解码与同一机构的多种写法。

清洗分四档，宁可少改不可错改：

- ``auto``   安全变换：去编号 / 上标、修重音、解实体、去尾部多余标记。
- ``review`` 像人名但不确定：尾部单字母去掉后对不上库里的同名，只提示。
- ``drop``   不是人名：邮箱、机构、地名、正文句子片段。
- ``keep``   不动。

真正写库前必须由用户确认；任何一档都要能在界面上逐条取消。
"""

from __future__ import annotations

import html
import re
import sqlite3
import unicodedata

# ---------------------------------------------------------------- 重音 / 实体

# PDF 抽字把重音符号打散成「符号 + 字母」（`´u`、`¸s`）。改写成
# 「字母 + 组合附加符」，再走 NFC 合成，两段文字才会变成同一个码位。
_ACCENT_SIGNS = {
    "´": "\u0301",  # acute
    "`": "\u0300",  # grave
    "¨": "\u0308",  # diaeresis
    "¸": "\u0327",  # cedilla
    "˘": "\u0306",  # breve
    "ˆ": "\u0302",  # circumflex
    "\u02dc": "\u0303",  # small tilde
}
_DOTTED_LESS_I = "ı"


def _fix_accents(text: str) -> str:
    # 先把无点 i 还原成 i，让 `´ı` 这类组合也能被下面那条规则接住
    text = text.replace(_DOTTED_LESS_I, "i")
    for sign, mark in _ACCENT_SIGNS.items():
        text = re.sub(
            re.escape(sign) + r"([A-Za-z])",
            lambda m, mk=mark: m.group(1) + mk,
            text,
        )
    return text


def _tidy(text: str) -> str:
    """解码实体、修重音、规范化空白；返回 NFC 之后的字符串。"""
    text = html.unescape(text or "")
    text = _fix_accents(text)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------- 词表

# 名字里合法的连写小写虚词（西班牙 / 葡萄牙 / 荷兰 / 意大利姓氏常见）
_PARTICLES = {
    "de", "del", "della", "der", "di", "da", "das", "do", "dos",
    "van", "von", "den", "le", "la", "lo", "du", "des", "bin", "al",
}

_ORG_WORDS = {
    "university", "institute", "department", "laboratory", "lab", "school",
    "college", "center", "centre", "inc", "corporation", "corp", "company",
    "group", "technologies", "technology", "research", "foundation",
    "polytechnic", "faculty", "ministry", "academy", "society", "committee",
}
_PLACES = {
    "usa", "u.s.a", "china", "portugal", "switzerland", "turkey", "taiwan",
    "canada", "italy", "spain", "uk", "u.k", "france", "germany", "japan",
    "korea", "india", "aveiro", "zurich", "zürich", "istanbul", "barcelona",
    "ottawa", "glasgow", "edinburgh", "atlanta", "nanjing", "germany",
}
_STOPWORDS = {
    "the", "we", "can", "of", "in", "to", "and", "is", "by", "with", "for",
    "on", "that", "this", "has", "have", "are", "was", "were", "be", "as",
    "at", "it", "from", "which", "there", "these", "such", "not", "but",
    "an", "a", "or", "if", "than", "more", "most", "our", "their", "its",
    "all", "any", "been", "being", "into", "over", "after", "before",
}

# 论文标题里的技术词。一条「人名」里命中两个以上，几乎必定是从标题里
# 错切出来的词组（`Feasible Region Shrinkage`、`Transformer-Coupled Resonators`）。
_TECH_WORDS = {
    "swarm", "algorithm", "algorithms", "feasible", "region", "shrinkage",
    "transformer", "coupled", "resonators", "resonator", "extended",
    "efficiency", "range", "nonuniform", "microstrip", "lines", "line",
    "network", "networks", "optimization", "design", "amplifier",
    "amplifiers", "power", "band", "bandwidth", "load", "modulation",
    "signal", "circuit", "circuits", "modeling", "model", "learning",
    "neural", "inverse", "method", "methodology", "analysis", "approach",
    "structure", "structures", "wave", "waves", "frequency", "gain",
    "system", "systems", "device", "devices", "array", "arrays",
}

# 标记字符：上标、脚注、归属字母编号、PDF 乱码
_SUPERSCRIPT_TAIL = "∗*†‡#+°∘"
_GARBAGE_CHARS = "\ufffd\u200b\u200e\u200f\ufeff\u00ad"
_TRAILING_MARKERS = re.compile(
    r"(?:[\s" + _SUPERSCRIPT_TAIL + _GARBAGE_CHARS + r"0-9,;:]+)$"
)
_LEADING_MARKERS = re.compile(
    r"^[{\[(\s" + _SUPERSCRIPT_TAIL + _GARBAGE_CHARS + r"△▲●○◆◇]+"
)


# ---------------------------------------------------------------- 作者

def _looks_like_person(name: str) -> bool:
    tokens = [t for t in name.split(" ") if t]
    if not tokens or len(tokens) > 5:
        return False
    low = name.lower()
    if "@" in name or "{" in name or "}" in name or "|" in name:
        return False
    if name.endswith((".", ";", ":", ",")):
        return False
    for word in _ORG_WORDS | _PLACES:
        if re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", low):
            return False

    tech_hits = 0
    for token in tokens:
        bare = token.strip(",;:.")
        if not bare:
            continue
        # 首字母缩写（`A.`、`J.`、`C.-C.`）不是虚词，先放行
        if re.fullmatch(r"[A-Z]\.?", bare) or re.fullmatch(r"[A-Z]\.-[A-Z]\.?", bare):
            continue
        word = bare.lower()
        if word in _PARTICLES:
            continue
        if word in _STOPWORDS:
            return False
        if word in _TECH_WORDS:
            tech_hits += 1
        # 正文片段：多词且整词全小写、长度 > 4，基本不是姓
        if len(tokens) >= 2 and bare.islower() and len(bare) > 4:
            return False

    if tech_hits >= 2:
        return False
    if len(tokens) >= 3 and tech_hits >= 1:
        return False
    # 单字地名（`Aveiro`、`Portugal`）不是人名
    if len(tokens) == 1 and low in _PLACES:
        return False
    if not any(t[:1].isupper() for t in tokens if t.strip(",;:.")):
        return False
    return True


def clean_author(name: str, known: set[str]) -> dict:
    """返回 ``{action, value, reason}``；action 为 keep / auto / review / drop。"""
    raw = name or ""
    tidy = _tidy(raw)
    if not tidy:
        return {"action": "drop", "value": "", "reason": "空名字"}

    if re.search(r"[\w.+-]+@[\w.-]+", tidy):
        return {"action": "drop", "value": "", "reason": "邮箱地址"}
    if not _looks_like_person(tidy):
        return {"action": "drop", "value": "", "reason": "正文片段 / 机构名"}

    reasons: list[str] = []

    # 1. 去首尾标记与编号
    base = _LEADING_MARKERS.sub("", tidy)
    base = _TRAILING_MARKERS.sub("", base).strip()
    if not base:
        return {"action": "review", "value": "", "reason": "只剩标记，需人工判断"}
    stripped = base != tidy
    if stripped:
        reasons.append("去掉上标 / 脚注标记")
    if tidy != raw.strip():
        reasons.append("修重音 / 解实体")

    # 2. 剥过标记链的名字，尾部常还剩一个字形的归属字母
    #    （`Liyuan Xuea∗` → `Liyuan Xuea`）。只有去掉后确实对得上库里
    #    已有作者时才动；否则保留——`Kaushik Sengupta#` 的 a 是姓氏的一部分。
    if stripped:
        tail = re.match(r"^(.*\S) ?[a-e]$", base)
        if tail:
            candidate = tail.group(1).strip()
            if candidate.lower() in known:
                base = candidate
                reasons.append("去掉尾部归属字母")
        return {"action": "auto", "value": base, "reason": "、".join(reasons)}

    # 3. 只有重音 / 实体变化
    if tidy != raw.strip():
        return {"action": "auto", "value": tidy, "reason": "、".join(reasons)}

    # 4. 光秃秃的尾部单字母（`Yuan Dingb`）：**只在库里已有同名时**才合并。
    #    没有任何证据就改，会把 `Jing Xia` 削成 `Jing Xi`——这类名字在库里
    #    真实存在（已被误伤过一次），所以无证据时一律 keep、不提建议。
    tail_letter = re.match(r"^(.*\S) ?[a-e]$", base)
    if tail_letter:
        candidate = tail_letter.group(1).strip()
        if candidate and _looks_like_person(candidate) and candidate.lower() in known:
            return {
                "action": "auto",
                "value": candidate,
                "reason": "去掉尾部归属字母（库里已有同名）",
            }

    return {"action": "keep", "value": tidy, "reason": ""}


# ---------------------------------------------------------------- 单位

_ORG_PREFIX = re.compile(r"^[#+△▲●○◆◇\s*†‡\ufffd]+")
_ORG_DANGLING = re.compile(
    r"(?i)\b(?:di|of|and|de|des|the|du|der|della|del|for|at|in|e)\s*$"
)


def clean_org(name: str) -> dict:
    raw = name or ""
    tidy = _tidy(raw)
    if not tidy:
        return {"action": "drop", "value": "", "reason": "空名字"}

    base = _ORG_PREFIX.sub("", tidy).strip()
    base = re.sub(r"\s*,\s*", ", ", base).strip(" ,;")
    if not base:
        return {"action": "drop", "value": "", "reason": "只剩标记"}

    reasons: list[str] = []
    if base != tidy:
        reasons.append("解实体 / 去前导标记")
    if tidy != raw.strip() and not reasons:
        reasons.append("解实体")

    if base != raw.strip():
        return {"action": "auto", "value": base, "reason": "、".join(reasons)}

    if len(base) < 8 or len(base.split(" ")) == 1:
        return {"action": "review", "value": base, "reason": "片段过短，可能被截断"}
    if _ORG_DANGLING.search(base):
        return {"action": "review", "value": base, "reason": "以虚词结尾，可能被截断"}

    return {"action": "keep", "value": base, "reason": ""}


# ---------------------------------------------------------------- 扫描 / 应用

def _count_items_author(conn: sqlite3.Connection, author_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(DISTINCT item_id) FROM item_author WHERE author_id = ?",
        (author_id,),
    ).fetchone()[0]


def scan(conn: sqlite3.Connection) -> dict:
    """扫一遍作者与单位，给出建议；不写库。"""
    author_rows = conn.execute(
        "SELECT id, display_name FROM author ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    known = {r["display_name"].strip().lower() for r in author_rows}

    authors = []
    for r in author_rows:
        verdict = clean_author(r["display_name"], known)
        if verdict["action"] == "keep":
            continue
        authors.append(
            {
                "id": r["id"],
                "name": r["display_name"],
                "action": verdict["action"],
                "suggest": verdict["value"],
                "reason": verdict["reason"],
                "count": _count_items_author(conn, r["id"]),
            }
        )

    org_rows = conn.execute(
        "SELECT id, name FROM org ORDER BY name COLLATE NOCASE"
    ).fetchall()
    orgs = []
    for r in org_rows:
        verdict = clean_org(r["name"])
        if verdict["action"] == "keep":
            continue
        count = conn.execute(
            "SELECT COUNT(DISTINCT item_id) FROM item_org WHERE org_id = ?", (r["id"],)
        ).fetchone()[0]
        orgs.append(
            {
                "id": r["id"],
                "name": r["name"],
                "action": verdict["action"],
                "suggest": verdict["value"],
                "reason": verdict["reason"],
                "count": count,
            }
        )

    return {
        "authors": authors,
        "orgs": orgs,
        "totals": {
            "authors": len(author_rows),
            "orgs": len(org_rows),
            "authorCandidates": len(authors),
            "orgCandidates": len(orgs),
        },
    }


def _merge_author_into(
    conn: sqlite3.Connection, from_id: str, to_name: str
) -> str:
    """把 from_id 的所有条目挪到 to_name，返回目标 id。"""
    to_name = to_name.strip()
    row = conn.execute(
        "SELECT id FROM author WHERE display_name = ? COLLATE NOCASE", (to_name,)
    ).fetchone()
    if row and row["id"] != from_id:
        to_id = row["id"]
    else:
        conn.execute(
            "UPDATE author SET display_name = ?, sort_name = ? WHERE id = ?",
            (to_name, to_name, from_id),
        )
        return from_id
    conn.execute(
        "INSERT OR IGNORE INTO item_author (item_id, author_id, position, role) "
        "SELECT item_id, ?, position, role FROM item_author WHERE author_id = ?",
        (to_id, from_id),
    )
    conn.execute("DELETE FROM item_author WHERE author_id = ?", (from_id,))
    conn.execute("DELETE FROM author WHERE id = ?", (from_id,))
    return to_id


def _merge_org_into(conn: sqlite3.Connection, from_id: str, to_name: str) -> str:
    to_name = to_name.strip()
    row = conn.execute(
        "SELECT id FROM org WHERE name = ? COLLATE NOCASE", (to_name,)
    ).fetchone()
    if row and row["id"] != from_id:
        to_id = row["id"]
    else:
        conn.execute(
            "UPDATE org SET name = ?, normalized_name = ? WHERE id = ?",
            (to_name, to_name.lower(), from_id),
        )
        return from_id
    conn.execute(
        "INSERT OR IGNORE INTO item_org (item_id, org_id, position) "
        "SELECT item_id, ?, position FROM item_org WHERE org_id = ?",
        (to_id, from_id),
    )
    conn.execute("DELETE FROM item_org WHERE org_id = ?", (from_id,))
    conn.execute("DELETE FROM org WHERE id = ?", (from_id,))
    return to_id


def apply(
    conn: sqlite3.Connection,
    author_ops: list[dict] | None = None,
    org_ops: list[dict] | None = None,
) -> dict:
    """执行用户确认过的动作。

    每条 op 形如 ``{"id": ..., "action": "rename"|"drop", "value": "..."}``。
    只动 op 里明确列出的行，没列到的一律不碰。
    """
    touched_items: set[str] = set()
    authors_renamed = authors_dropped = 0
    orgs_renamed = orgs_dropped = 0

    for op in author_ops or []:
        author_id = op.get("id")
        if not author_id:
            continue
        row = conn.execute("SELECT id FROM author WHERE id = ?", (author_id,)).fetchone()
        if not row:
            continue
        touched_items.update(
            r["item_id"]
            for r in conn.execute(
                "SELECT item_id FROM item_author WHERE author_id = ?", (author_id,)
            )
        )
        if op.get("action") == "drop":
            conn.execute("DELETE FROM item_author WHERE author_id = ?", (author_id,))
            conn.execute("DELETE FROM author WHERE id = ?", (author_id,))
            authors_dropped += 1
        else:
            value = (op.get("value") or "").strip()
            if not value:
                continue
            _merge_author_into(conn, author_id, value)
            authors_renamed += 1

    for op in org_ops or []:
        org_id = op.get("id")
        if not org_id:
            continue
        row = conn.execute("SELECT id FROM org WHERE id = ?", (org_id,)).fetchone()
        if not row:
            continue
        touched_items.update(
            r["item_id"]
            for r in conn.execute(
                "SELECT item_id FROM item_org WHERE org_id = ?", (org_id,)
            )
        )
        if op.get("action") == "drop":
            conn.execute("DELETE FROM item_org WHERE org_id = ?", (org_id,))
            conn.execute("DELETE FROM org WHERE id = ?", (org_id,))
            orgs_dropped += 1
        else:
            value = (op.get("value") or "").strip()
            if not value:
                continue
            _merge_org_into(conn, org_id, value)
            orgs_renamed += 1

    # 作者 / 单位变动会影响「待补全」判定
    from .repo import _recompute_review

    for item_id in touched_items:
        _recompute_review(conn, item_id)
    conn.commit()

    return {
        "authorsRenamed": authors_renamed,
        "authorsDropped": authors_dropped,
        "orgsRenamed": orgs_renamed,
        "orgsDropped": orgs_dropped,
        "itemsTouched": len(touched_items),
    }
