from __future__ import annotations

import difflib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

UA = "Colophon/0.1 (local literature manager)"
TIMEOUT = 8

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"(?:arxiv[:/\s]|abs/)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
ARXIV_BARE_RE = re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")
PATENT_RE = re.compile(
    r"\b(WO\s?\d{4}/?\d{5,6}|EP\s?\d{7}|US\s?\d{7,8}|CN\s?\d{9})\s?[A-Z]\d?\b",
    re.I,
)
YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")

MIN_TEXT_CHARS = 200       # 少于这个长度基本就是没有文本层
MAX_PROMPT_CHARS = 7000    # 送模型的首页文本上限
MAX_IMAGE_WIDTH = 1600     # 首页渲染后的像素宽度上限，控制识图 token
VISION_DPI = 150
TITLE_MATCH = 0.9          # 标题反查 Crossref 的最低相似度，低于这个宁可不要
MIN_YEAR, MAX_YEAR = 1800, date.today().year + 1

JUNK_VENUE_RE = re.compile(
    r"^(?:pdf|patent\s*pdf|unknown|untitled|none|n/?a|microsoft(\s+word)?|"
    r"adobe.*|scan(ned)?.*|document|paper)$",
    re.I,
)
JUNK_TITLE_RE = re.compile(
    r"^(?:untitled|no\s*title|document\d*|microsoft\s+word.*|.*\.(?:pdf|docx?|tex|dvi))$",
    re.I,
)
JUNK_AUTHOR_RE = re.compile(
    r"^(?:unknown|administrator|admin|user|owner|author|microsoft.*|adobe.*|.*patsnap.*)$",
    re.I,
)
GENERIC_TITLES = {
    "paper", "article", "document", "untitled", "download", "full text",
    "main", "manuscript", "preprint", "thesis", "report",
}


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _norm_title(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", (text or "").lower()))


def _valid_year(value) -> int | None:
    try:
        year = int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return None
    return year if MIN_YEAR <= year <= MAX_YEAR else None


# ---------------------------------------------------------------- 识别

def detect_identifiers(text: str) -> tuple[str, str]:
    """返回 (doi, arxiv_id)。先找 DOI，再找 arXiv 编号。"""
    text = text or ""
    doi = ""
    match = DOI_RE.search(text)
    if match:
        doi = match.group(0).rstrip(".,;)]}")

    arxiv = ""
    match = ARXIV_RE.search(text)
    if match:
        arxiv = match.group(1)
    elif not doi:
        match = ARXIV_BARE_RE.search(text)
        if match:
            arxiv = match.group(1)

    if not doi and arxiv:
        doi = f"10.48550/arXiv.{arxiv}"
    return doi, arxiv


def detect_patent(text: str) -> str:
    """从文本里找 WO / EP / US 专利公告号。"""
    match = PATENT_RE.search(text or "")
    if not match:
        return ""
    return re.sub(r"[^A-Z0-9]", "", match.group(1).upper())


def _patent_meta(number: str) -> dict | None:
    num = re.sub(r"[^A-Z0-9]", "", (number or "").upper())
    match = re.match(r"([A-Z]{2})(\d+)", num)
    if not match:
        return None
    office, digits = match.group(1), match.group(2)
    year = None
    if office == "WO" and len(digits) >= 8:
        # WO2009/081341：编号本身带公开年份
        year = _valid_year(digits[:4])
    venue = {"WO": "WIPO", "EP": "EPO", "US": "USPTO", "CN": "CNIPA"}.get(
        office, office
    )
    return {
        "source": "专利号",
        "title": "",
        "abstract": "",
        "authors": [],
        "orgs": [],
        "year": year,
        "venue": venue,
        "doi": "",
        "arxiv_id": "",
        "url": "https://patents.google.com/patent/" + num,
        "patent": num,
    }


# ---------------------------------------------------------------- PDF

def _open_pymupdf(path: Path):
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        return pymupdf.open(str(path))
    except Exception:
        return None


def read_pdf_info(path: Path | None) -> dict:
    """读 PDF 内嵌的 DocInfo 字段，键统一成小写。

    论文和专利的 PDF 往往自带标题、期刊、DOI、专利号；先读这里比先跑模型便宜得多。
    """
    if path is None or not path.exists():
        return {}

    info: dict[str, str] = {}
    try:
        from pypdf import PdfReader
        for key, value in (PdfReader(str(path)).metadata or {}).items():
            text = _clean(str(value or ""))
            if text:
                info[str(key).lstrip("/").lower()] = text
    except Exception:
        pass

    doc = _open_pymupdf(path)
    if doc is not None:
        try:
            for key, value in (doc.metadata or {}).items():
                text = _clean(str(value or ""))
                if text:
                    info[str(key).lower()] = text
        finally:
            doc.close()
    return info


def pdf_text(path: Path, max_pages: int = 2) -> str:
    """抽前几页文本。优先 PyMuPDF（双栏排版更准），退回 pypdf。"""
    doc = _open_pymupdf(path)
    if doc is not None:
        try:
            chunks = []
            for page in doc[:max_pages]:
                chunks.append(page.get_text() or "")
            text = "\n".join(chunks)
            if text.strip():
                return text
        except Exception:
            pass
        finally:
            doc.close()

    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
    except Exception:
        return ""


def render_page(path: Path, page_index: int = 0, dpi: int = VISION_DPI) -> bytes:
    """把某一页渲染成 PNG，给识图模型用。没有 PyMuPDF 就返回空。"""
    try:
        import pymupdf
    except ImportError:
        return b""
    doc = _open_pymupdf(path)
    if doc is None:
        return b""
    try:
        if page_index < 0 or page_index >= doc.page_count:
            return b""
        page = doc[page_index]
        width = page.rect.width or 595.0
        zoom = min(dpi / 72.0, MAX_IMAGE_WIDTH / width)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")
    except Exception:
        return b""
    finally:
        doc.close()


# ---------------------------------------------------------------- 内嵌信息

def _usable_title(value: str) -> str:
    title = _clean(value).strip(" ._-")
    if len(title) < 8 or len(title) > 400:
        return ""
    if DOI_RE.search(title) or ARXIV_RE.search(title) or PATENT_RE.search(title):
        return ""
    if JUNK_TITLE_RE.match(title):
        return ""
    if title.casefold() in GENERIC_TITLES:
        return ""
    if sum(ch.isalpha() for ch in title) < 6:
        return ""
    return title


def _usable_venue(value: str) -> str:
    venue = _clean(value).strip(" .,;")
    if len(venue) < 3 or len(venue) > 200:
        return ""
    if JUNK_VENUE_RE.match(venue):
        return ""
    if DOI_RE.search(venue) or ARXIV_RE.search(venue) or PATENT_RE.search(venue):
        return ""
    if re.search(r"https?://|www\.|@", venue, re.I):
        return ""
    if sum(ch.isalpha() for ch in venue) < 3:
        return ""
    return venue


def _usable_authors(value: str) -> list[str]:
    raw = _clean(value)
    if not raw or re.search(r"https?://|www\.|@", raw, re.I):
        return []
    parts = re.split(r"\s*[;；]\s*", raw)
    if len(parts) == 1 and raw.count(",") >= 2:
        parts = [p.strip() for p in raw.split(",")]
    authors = []
    for name in parts:
        name = _clean(name)
        if not (2 <= len(name) <= 80):
            continue
        if JUNK_AUTHOR_RE.match(name):
            continue
        if sum(ch.isalpha() for ch in name) < 2:
            continue
        authors.append(name)
    return authors[:30]


def _hints_from_info(info: dict, filename: str) -> dict:
    hints: dict = {"authors": [], "orgs": []}
    title = _usable_title(info.get("title") or "")
    if title:
        hints["title"] = title

    subject = info.get("subject") or ""
    for piece in (_clean(p) for p in re.split(r"[;；]", subject)):
        if not piece:
            continue
        if not hints.get("year"):
            year = _valid_year(piece) if re.fullmatch(r"\d{4}", piece) else None
            if year:
                hints["year"] = year
        if not hints.get("venue"):
            venue = _usable_venue(piece)
            if venue:
                hints["venue"] = venue

    authors = _usable_authors(info.get("author") or "")
    if authors:
        hints["authors"] = authors
    return hints


LOCAL_TEXT_MAX_LINES = 80
LOCAL_ORG_RE = re.compile(
    r"\b(?:department|dept\.?|university|institute|institution|school|college|"
    r"laboratory|laboratories|lab\.?|center|centre|hospital|faculty|academy|"
    r"polytechnic|corporation|company|"
    r"inc\.?|ltd\.?|national|observatory|foundation|instituto|istituto|"
    r"universit(?:y|à|a)|politecnico|dipartimento|laboratoire|laboratorio|"
    r"école|ecole)\b"
    r"|(?:大学|学院|研究院|研究所|实验室|中心|医院|科学院|学校|公司|研究中心)",
    re.I,
)
LOCAL_VENUE_RE = re.compile(
    r"\b(?:IEEE|ACM|Springer|Elsevier|Transactions|Journal|Proceedings|"
    r"Conference|Workshop|Symposium|Letters|Review|Magazine)\b",
    re.I,
)
LOCAL_STOP_RE = re.compile(
    r"^(?:abst\w*|摘要|index\s+terms?|keywords?|key\s+words?|"
    r"i\.?\s*introduction|introduction|1\.?\s+introduction)\b",
    re.I,
)
LOCAL_ROLE_RE = re.compile(
    r"(?:IEEE\s+)?(?:student|senior|associate|fellow|life|graduate|"
    r"undergraduate)?\s*member\b|\bIEEE\b",
    re.I,
)
LOCAL_CREDENTIAL_RE = re.compile(
    r"\b(?:IEEE\s+)?(?:student|senior|associate|life|graduate|undergraduate)?"
    r"\s*(?:member|fellow)\b|\bIEEE\b",
    re.I,
)


def _local_line(value: str) -> str:
    """清理文本层常见的连字、软连字符和多余空白。"""
    value = (value or "").translate(
        str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
    )
    return _clean(value.replace("\u00ad", ""))


def _local_lines(text: str) -> list[str]:
    return [line for line in (_local_line(line) for line in (text or "").splitlines()) if line]


def _is_page_marker(line: str) -> bool:
    return bool(
        re.fullmatch(r"(?:\d{1,4}|[A-Z]?\s*-\s*\d{1,4}|[A-Z0-9]{1,8}-\d{1,4})", line)
    )


def _is_local_header(line: str) -> bool:
    if _is_page_marker(line):
        return True
    # 作者署名经常含有 “Fellow, IEEE” 或 “IEEE Member”；这不是期刊页眉。
    if re.search(r"\b(?:member|fellow)\b", line, re.I) and (
        "," in line or re.search(r"\s+(?:and|&)\s+", line, re.I)
    ):
        return False
    # 作者行常带有 “IEEE Member”；不能因为其中出现 IEEE 就把它当成期刊页眉。
    if (
        LOCAL_ROLE_RE.search(line)
        and re.search(r"\bmember\b", line, re.I)
        and not re.search(
            r"\b(?:transactions|journal|proceedings|conference|workshop|"
            r"symposium|letters|review|magazine)\b",
            line,
            re.I,
        )
    ):
        return False
    if LOCAL_ROLE_RE.search(line) and not LOCAL_VENUE_RE.search(line) and not re.search(
        r"\b(?:vol\.?|no\.?|issue|transactions?)\b", line, re.I
    ):
        return False
    # 期刊头部通常位于标题前；排除它，避免把刊名当成论文标题或作者单位。
    return bool(LOCAL_VENUE_RE.search(line)) and not bool(
        re.search(r"\b(?:department|university|institute|laboratory|college)\b", line, re.I)
    )


def _is_local_org(line: str) -> bool:
    if not line or len(line) < 4 or len(line) > 240:
        return False
    if LOCAL_STOP_RE.match(line) or _is_page_marker(line):
        return False
    if re.search(r"(?:https?://|www\.|\bdoi\b|@)", line, re.I):
        return False
    if re.fullmatch(r"[\d\s.,:/()\-]+", line):
        return False
    if re.search(
        r"^(?:via|viale|street|st\.?|road|rd\.?|avenue|ave\.?|"
        r"route|box|p\.?o\.?\s+box)\b",
        line,
        re.I,
    ):
        return False
    if re.match(
        r"^(?:this work|this paper|the authors|supported|corresponding author|"
        r"email|e-mail)\b",
        line,
        re.I,
    ):
        return False
    if re.search(r"\b(?:vol\.?|no\.?|issue|october|january|february|march|april|"
                 r"may|june|july|august|september|november|december)\b", line, re.I):
        return False
    return bool(LOCAL_ORG_RE.search(line))


def _clean_local_org(line: str) -> str:
    line = re.sub(r"^\s*[\d*†‡]+\s*[.)\-:]?\s*", "", line)
    line = re.sub(r"^\s*[a-z]\s*[.)\-:]\s*", "", line)
    line = re.sub(r"^\s*[a-z](?=[A-Z])", "", line)
    return line.strip(" ,.;:")


def _is_local_junk(line: str) -> bool:
    """首页标题前的 DOI、日期和版权信息，不属于论文题名。"""
    return bool(
        re.match(
            r"^(?:received\b|accepted\b|date of publication\b|date of current version\b|"
            r"digital object identifier\b|doi\s*[:.]?|copyright\b|issn\b|"
            r"published\b|available online\b|preprint\b|accepted manuscript\b|"
            r"author manuscript\b)",
            line,
            re.I,
        )
        or re.search(r"\bdoi\b|10\.\d{4,9}/", line, re.I)
    )


def _looks_like_local_author(line: str) -> bool:
    if (
        not line
        or LOCAL_STOP_RE.match(line)
        or _is_local_org(line)
        or _is_local_header(line)
        or _is_local_junk(line)
    ):
        return False
    if re.fullmatch(
        r"(?:ieee|member|fellow|student|senior|associate|graduate|"
        r"undergraduate|researcher)(?:\s+member)?",
        line,
        re.I,
    ):
        return False
    if re.search(
        r"^(?:via|viale|street|st\.?|road|rd\.?|avenue|ave\.?|"
        r"route|box|p\.?o\.?\s+box)\b|\b\d{4,6}\b",
        line,
        re.I,
    ):
        return False
    if re.search(r"\d", line) and not (
        LOCAL_ROLE_RE.search(line) or re.search(r"[*†‡△+]", line)
    ):
        return False
    if LOCAL_ROLE_RE.search(line):
        return True
    if re.search(r"\s+(?:and|&)\s+", line, re.I):
        parts = re.split(r"\s+(?:and|&)\s+", line, flags=re.I)
        if not len(parts) >= 2 or not all(2 <= len(part.strip()) <= 100 for part in parts):
            return False
        # 多行标题也会出现 “... and ...”；作者的每一段应更像姓名，
        # 而不是以小写介词开头或含有标题术语。
        title_words = {
            "abstract", "approach", "analysis", "automatic", "circuit", "design",
            "learning", "method", "model", "modeling", "network", "neural",
            "optimization", "power", "system", "using", "with", "based", "for",
            "from", "line", "tuning", "experimental", "validation", "theoretical",
            "synthesis", "transformer", "transformers", "structure", "structures",
            "circuits", "matching", "overview", "performance", "part", "study",
            "passive", "passives", "amplifier", "amplifiers", "geometrical", "constraint",
            "overlap", "algorithm", "algorithms", "text", "layer", "metadata",
            "test", "data", "science", "technology", "continuous", "boundary",
            "broadband", "doherty", "output", "frequency", "filter", "inversion",
            "artificial", "intelligence", "assisted", "mixed", "mode", "field",
        }
        for part in parts:
            words = re.findall(r"[A-Za-z][A-Za-z.'-]*|[\u4e00-\u9fff]+", part)
            if not words or words[0][:1].islower():
                return False
            if any(
                piece in title_words
                for word in words
                for piece in re.findall(r"[a-z]+", word.lower())
            ):
                return False
        return True
    if "," in line and len(line) <= 220 and sum(ch.isalpha() for ch in line) >= 6:
        return True

    # 没有逗号的单作者行（例如 “Nicolaos G. Alexopoulos”）也很常见。
    # 用标题词排除 “A Neural Network ...” 这类多行标题，避免把标题当作者。
    title_words = {
        "abstract", "approach", "analysis", "automatic", "circuit", "design",
        "learning", "method", "model", "network", "neural", "optimization",
        "power", "system", "using", "with", "based", "for", "from", "experimental",
        "validation", "theoretical", "synthesis", "structure", "structures", "matching",
        "overview", "performance", "part", "study", "passive", "amplifier", "overlap",
        "text", "layer", "metadata", "test", "data", "science", "technology",
        "continuous", "boundary", "broadband", "doherty", "output", "frequency",
        "filter", "inversion", "artificial", "intelligence", "assisted", "mixed",
        "mode", "field", "network", "line",
    }
    words = re.findall(r"[A-Za-z][A-Za-z.'-]*|[\u4e00-\u9fff]+", line)
    if not 2 <= len(words) <= 5:
        return False
    if any(
        piece in title_words
        for word in words
        for piece in re.findall(r"[a-z]+", word.lower())
    ):
        return False
    return all(
        word[:1].isupper() or (len(word) <= 3 and word.replace(".", "").isupper())
        for word in words
    )


def _split_local_authors(line: str) -> list[str]:
    cleaned = re.sub(r"\([^)]*(?:member|fellow|IEEE)[^)]*\)", " ", line, flags=re.I)
    cleaned = LOCAL_CREDENTIAL_RE.sub(" ", cleaned)
    cleaned = re.sub(r"^\s*[\d*†‡]+\s*(?:[,.;:)\-]\s*)?", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    parts = re.split(r"\s*(?:;|,|\band\b|&)\s*", cleaned, flags=re.I)
    authors: list[str] = []
    for part in parts:
        name = part.strip(" .")
        name = re.sub(r"\s+[a-z*†‡]$", "", name).strip(" .")
        if not (2 <= len(name) <= 80):
            continue
        if _is_local_org(name) or re.search(
            r"\b(?:member|author|fellow|graduate|student|senior|associate)\b",
            name,
            re.I,
        ):
            continue
        words = re.findall(r"[A-Za-z][A-Za-z.'-]*|[\u4e00-\u9fff]+", name)
        if len(words) >= 2 or (len(words) == 1 and len(words[0]) >= 4):
            authors.append(name)
    return list(dict.fromkeys(authors))[:30]


def _local_title(lines: list[str]) -> tuple[str, int]:
    """找到首页标题及其结束位置，返回 (标题, 下一行索引)。"""
    author_index = _local_author_start(lines)
    if author_index is not None:
        # 标题位于首页最后一个页眉 / 页码之后、第一条作者行之前。
        start = 0
        for index, line in enumerate(lines[:author_index]):
            if _is_page_marker(line) or _is_local_header(line) or _is_local_junk(line):
                start = index + 1
        title_lines = [
            line for line in lines[start:author_index]
            if (
                len(line) >= 3
                and not _is_page_marker(line)
                and not _is_local_header(line)
                and not _is_local_junk(line)
            )
        ]
        title = _usable_title(" ".join(title_lines))
        if title:
            return title, author_index

    # 没有明确作者行时，保守地找一个不属于页眉、单位或正文分节的候选，
    # 同时吸收紧邻的续行，避免把多行标题截成半句。
    for index, line in enumerate(lines[:LOCAL_TEXT_MAX_LINES]):
        if (
            len(line) < 8
            or LOCAL_STOP_RE.match(line)
            or _is_page_marker(line)
            or _is_local_header(line)
            or _is_local_org(line)
            or _is_local_junk(line)
            or re.search(r"(?:https?://|www\.|\bdoi\b)", line, re.I)
        ):
            continue
        pieces = [line]
        next_index = index + 1
        while next_index < min(len(lines), index + 6):
            following = lines[next_index]
            if (
                LOCAL_STOP_RE.match(following)
                or _is_page_marker(following)
                or _is_local_header(following)
                or _is_local_org(following)
                or _looks_like_local_author(following)
                or _is_local_junk(following)
            ):
                break
            if len(following) < 3:
                break
            pieces.append(following)
            next_index += 1
        title = _usable_title(" ".join(pieces))
        if title:
            return title, next_index
    return "", 0


def _local_author_start(lines: list[str], limit: int = 24) -> int | None:
    for index, line in enumerate(lines[:limit]):
        if LOCAL_STOP_RE.match(line):
            break
        # 没有刊头时，首页第一行通常就是标题；不要把一个由首字母组成的
        # 标题误判成单作者名。真正的首作者行会出现在标题之后。
        if index == 0 and not LOCAL_ROLE_RE.search(line):
            continue
        if _looks_like_local_author(line):
            # 单独一行的姓名需要紧邻单位或署名角色；否则很可能是标题的
            # 一行续文（例如 “Fourier-Series-Based Continuous-Boundary”）。
            if (
                LOCAL_ROLE_RE.search(line)
                or re.search(r"\s+(?:and|&)\s+|,", line, re.I)
                or any(_is_local_org(next_line) for next_line in lines[index + 1 : index + 3])
                or any(
                    LOCAL_ROLE_RE.search(next_line)
                    for next_line in lines[index + 1 : index + 3]
                )
            ):
                return index
    return None


def _local_year(lines: list[str]) -> int | None:
    # 先取刊头日期，跳过 IEEE 页码（例如 “1914”）。
    for line in lines[:30]:
        if _is_page_marker(line) or _is_local_junk(line):
            continue
        if re.search(
            r"\b(?:january|february|march|april|may|june|july|august|"
            r"september|october|november|december)\b",
            line,
            re.I,
        ):
            match = YEAR_RE.search(line)
            if match:
                return _valid_year(match.group(1))
    for line in lines[:20]:
        if _is_page_marker(line) or _is_local_junk(line):
            continue
        match = YEAR_RE.search(line)
        if match:
            year = _valid_year(match.group(1))
            if year:
                return year
    return None


def _local_text_meta(text: str, filename: str = "", title_hint: str = "") -> dict:
    """从 PDF 文本层首页提取可直接使用的元数据，不访问网络。"""
    lines = _local_lines(text)
    if not lines:
        return {}

    title, title_end = _local_title(lines)
    author_start = _local_author_start(lines)
    if author_start is not None:
        title_end = author_start
    authors: list[str] = []
    author_scan_start = author_start if author_start is not None else title_end
    for line in lines[author_scan_start : min(len(lines), author_scan_start + 20)]:
        if LOCAL_STOP_RE.match(line) or re.match(
            r"^(?:corresponding author|this work|acknowledg|supported|funded|"
            r"email|e-mail)\b",
            line,
            re.I,
        ):
            break
        if _is_local_org(line):
            continue
        if _looks_like_local_author(line):
            authors.extend(_split_local_authors(line))
        if authors and len(authors) >= 30:
            break

    orgs: list[str] = []
    if author_start is not None:
        org_start = author_start
        for line in lines[org_start : min(len(lines), org_start + 24)]:
            if LOCAL_STOP_RE.match(line) or re.match(
                r"^(?:corresponding author|this work|acknowledg|supported|funded|"
                r"email|e-mail)\b",
                line,
                re.I,
            ):
                break
            for piece in re.split(r"\s*;\s*", line):
                name = _clean_local_org(piece)
                if _is_local_org(name):
                    if name and name.casefold() not in {org.casefold() for org in orgs}:
                        orgs.append(name)

    venue = ""
    for line in lines[: max(title_end, 12)]:
        if _is_local_header(line) and not _is_page_marker(line):
            candidate = _usable_venue(line)
            if candidate:
                venue = candidate
                break

    doi, arxiv_id = detect_identifiers(text)
    year = _local_year(lines)
    patent = detect_patent(text)

    meta = {
        "source": "PDF 文本层",
        "title": title,
        "authors": list(dict.fromkeys(authors))[:30],
        "orgs": orgs[:30],
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": "",
    }
    if patent:
        meta["patent"] = patent
    return {
        key: value
        for key, value in meta.items()
        if key == "source" or value not in (None, "", [])
    }


def _filename_title(filename: str) -> str:
    stem = Path(filename or "").stem
    stem = re.sub(r"^[0-9a-f]{6,12}[_\-\s]+", "", stem, flags=re.I)
    return _clean(re.sub(r"[_]+", " ", stem))


def _title_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", (value or "").lower()))


def is_placeholder_title(title: str, filename: str) -> bool:
    """标题还是导入时从文件名生成的占位值，可以放心让新结果覆盖。"""
    key = _title_key(title)
    if not key:
        return True
    if key in GENERIC_TITLES:
        return True
    stem = Path(filename or "").stem
    candidates = {_title_key(stem), _title_key(_filename_title(filename))}
    candidates.discard("")
    return key in candidates


# ---------------------------------------------------------------- 数据源

def fetch_arxiv(arxiv_id: str) -> dict | None:
    url = "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id)
    xml = _get(url, accept="application/atom+xml")
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml)
    entry = root.find("a:entry", ns)
    if entry is None:
        return None

    title = _clean(entry.findtext("a:title", "", ns))
    if not title:
        return None
    abstract = _clean(entry.findtext("a:summary", "", ns))
    authors = [_clean(a.findtext("a:name", "", ns)) for a in entry.findall("a:author", ns)]
    published = entry.findtext("a:published", "", ns) or ""
    year = int(published[:4]) if published[:4].isdigit() else None
    doi = (entry.findtext("x:doi", "", ns) or "").strip() or f"10.48550/arXiv.{arxiv_id}"
    venue = _clean(entry.findtext("x:journal_ref", "", ns)) or "arXiv"

    return {
        "source": "arXiv",
        "title": title,
        "abstract": abstract,
        "authors": [a for a in authors if a],
        "orgs": [],
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def _crossref_orgs(msg: dict) -> list[str]:
    """从 Crossref 作者的 affiliation 中提取单位并去重。"""
    orgs: list[str] = []
    seen: set[str] = set()
    for person in msg.get("author") or []:
        affiliations = person.get("affiliation") or []
        if isinstance(affiliations, dict):
            affiliations = [affiliations]
        for affiliation in affiliations:
            if isinstance(affiliation, dict):
                name = affiliation.get("name") or ""
            else:
                name = affiliation
            name = _clean(str(name or ""))
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                orgs.append(name)
    return orgs


def _crossref_meta(msg: dict, doi: str) -> dict | None:
    titles = msg.get("title") or []
    title = _clean(titles[0]) if titles else ""
    if not title:
        return None

    authors = []
    for person in msg.get("author") or []:
        name = _clean(" ".join(p for p in (person.get("given"), person.get("family")) if p))
        if name:
            authors.append(name)

    year = None
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break

    venue = ""
    for key in ("container-title", "event", "publisher"):
        value = msg.get(key)
        if isinstance(value, list) and value:
            venue = _clean(str(value[0]))
            break
        if isinstance(value, str) and value:
            venue = _clean(value)
            break

    abstract = re.sub(r"<[^>]+>", " ", msg.get("abstract") or "")
    abstract = _clean(abstract)

    return {
        "source": "Crossref",
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "orgs": _crossref_orgs(msg),
        "year": year,
        "venue": venue,
        "doi": (msg.get("DOI") or doi).lower(),
        "arxiv_id": "",
        "url": (msg.get("URL") or f"https://doi.org/{doi}"),
    }


def fetch_crossref(doi: str) -> dict | None:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")
    data = json.loads(_get(url))
    return _crossref_meta(data.get("message") or {}, doi)


def search_crossref(title: str) -> dict | None:
    """按标题反查 Crossref；相似度不够高就当作没找到，避免抓到同主题的另一篇。"""
    query = _clean(re.sub(r"[_]+", " ", title or ""))
    key = _norm_title(query)
    if not key or (len(key) < 12 and not re.search(r"[\u4e00-\u9fff]", key)):
        return None
    if key in GENERIC_TITLES:
        return None

    url = (
        "https://api.crossref.org/works?rows=5"
        "&select=DOI,title,author,issued,container-title,abstract,URL"
        "&query.bibliographic=" + urllib.parse.quote(query)
    )
    data = json.loads(_get(url))
    best = None
    best_ratio = 0.0
    for item in (data.get("message") or {}).get("items") or []:
        titles = item.get("title") or []
        candidate = _clean(titles[0]) if titles else ""
        if not candidate:
            continue
        ratio = difflib.SequenceMatcher(None, _norm_title(candidate), key).ratio()
        if ratio > best_ratio:
            best, best_ratio = item, ratio
    if best is None or best_ratio < TITLE_MATCH:
        return None
    return _crossref_meta(best, (best.get("DOI") or "").strip())


def _openalex_abstract(index: dict | None) -> str:
    if not index:
        return ""
    positions: dict[int, str] = {}
    for word, places in index.items():
        for pos in places:
            positions[pos] = word
    return " ".join(positions[i] for i in sorted(positions))


def fetch_openalex(doi: str) -> dict | None:
    url = "https://api.openalex.org/works/doi:" + urllib.parse.quote(doi, safe="/")
    data = json.loads(_get(url))
    title = _clean(data.get("title") or "")
    if not title:
        return None

    authors = []
    orgs = []
    for authorship in data.get("authorships") or []:
        name = _clean((authorship.get("author") or {}).get("display_name") or "")
        if name:
            authors.append(name)
        for inst in authorship.get("institutions") or []:
            inst_name = _clean(inst.get("display_name") or "")
            if inst_name and inst_name not in orgs:
                orgs.append(inst_name)

    location = data.get("primary_location") or {}
    source = location.get("source") or {}
    venue = _clean(source.get("display_name") or "")
    raw_doi = (data.get("doi") or "").replace("https://doi.org/", "")

    return {
        "source": "OpenAlex",
        "title": title,
        "abstract": _openalex_abstract(data.get("abstract_inverted_index")),
        "authors": authors,
        "orgs": orgs,
        "year": data.get("publication_year"),
        "venue": venue,
        "doi": raw_doi.lower(),
        "arxiv_id": "",
        "url": raw_doi and f"https://doi.org/{raw_doi}" or "",
    }


def _lookup_online(doi: str, arxiv: str, patent: str) -> tuple[dict | None, list[str]]:
    meta = None
    errors: list[str] = []
    if arxiv:
        try:
            meta = fetch_arxiv(arxiv)
        except Exception as exc:
            errors.append(f"arXiv: {exc}")
    if meta is None and doi:
        try:
            meta = fetch_crossref(doi)
        except Exception as exc:
            errors.append(f"Crossref: {exc}")
    if meta is None and doi:
        try:
            meta = fetch_openalex(doi)
        except Exception as exc:
            errors.append(f"OpenAlex: {exc}")
    if meta is None and patent:
        meta = _patent_meta(patent)
    return meta, errors


# ---------------------------------------------------------------- 汇总

def missing_fields(meta: dict) -> list[str]:
    missing = []
    if not _clean(meta.get("title") or ""):
        missing.append("title")
    if not meta.get("authors"):
        missing.append("authors")
    if not meta.get("year"):
        missing.append("year")
    if not (meta.get("venue") or meta.get("doi") or meta.get("arxiv_id")):
        missing.append("venue")
    if not meta.get("orgs"):
        missing.append("orgs")
    return missing


def _protected(current: dict, filename: str) -> dict:
    """AI 不许覆盖用户已经确认过的字段；标题是文件名占位值时除外。"""
    current = current or {}
    title = current.get("title") or ""
    return {
        "title": bool(_clean(title)) and not is_placeholder_title(title, filename),
        "year": bool(current.get("year")),
        "venue": bool(_clean(current.get("venue") or "")),
        "doi": bool(_clean(current.get("doi") or "")),
        "arxiv_id": bool(_clean(current.get("arxiv_id") or "")),
        "authors": bool(current.get("authors")),
        "orgs": bool(current.get("orgs")),
    }


def _merge_meta(base: dict, extra: dict, protected: dict) -> list[str]:
    """只补空字段，返回补进来的字段名。"""
    added: list[str] = []
    for key in ("title", "year", "venue", "url"):
        if base.get(key) in (None, ""):
            value = extra.get(key)
            if value not in (None, "") and not protected.get(key):
                base[key] = value
                added.append(key)
    for key in ("doi", "arxiv_id"):
        if not base.get(key) and not protected.get(key):
            value = _clean(extra.get(key) or "")
            if value:
                base[key] = value
                added.append(key)
    if not base.get("authors") and not protected.get("authors"):
        authors = [a for a in (extra.get("authors") or []) if _clean(a)]
        if authors:
            base["authors"] = authors
            added.append("authors")
    if not base.get("orgs") and not protected.get("orgs"):
        orgs = [o for o in (extra.get("orgs") or []) if _clean(o)]
        if orgs:
            base["orgs"] = orgs
            added.append("orgs")
    return added


def _has_source(meta: dict, source: str) -> bool:
    return source in {
        part.strip() for part in (meta.get("source") or "").split("+") if part.strip()
    }


def _enrich_online(
    meta: dict,
    current: dict,
    filename: str,
    errors: list[str],
) -> None:
    """补齐第一数据源通常没有提供的作者单位。

    arXiv 和 Crossref 能命中一条记录，不代表它们提供了 affiliation。
    OpenAlex 的 authorships.institutions 才是当前流水线的主要单位来源，
    因此不能在第一次回源成功后直接停止。
    """
    doi = _clean(meta.get("doi") or "")
    if not doi:
        return

    # Crossref / arXiv 命中后继续查 OpenAlex；OpenAlex 自己命中且没有单位时，
    # 再反查 Crossref，利用其偶尔会返回的 author.affiliation。
    if not meta.get("orgs") and not _has_source(meta, "OpenAlex"):
        try:
            extra = fetch_openalex(doi)
        except Exception as exc:
            errors.append(f"OpenAlex 补单位: {exc}")
            extra = None
        if extra:
            added = _merge_meta(meta, extra, _protected(current, filename))
            if added:
                meta["source"] = _join_source(meta.get("source", ""), "OpenAlex")

    if not meta.get("orgs") and not _has_source(meta, "Crossref"):
        try:
            extra = fetch_crossref(doi)
        except Exception as exc:
            errors.append(f"Crossref 补单位: {exc}")
            extra = None
        if extra:
            added = _merge_meta(meta, extra, _protected(current, filename))
            if added:
                meta["source"] = _join_source(meta.get("source", ""), "Crossref")


def _join_source(current: str, extra: str) -> str:
    parts = [p.strip() for p in (current or "").split("+") if p.strip()]
    if extra and extra not in parts:
        parts.append(extra)
    return " + ".join(parts)


def _needs_ai(
    meta: dict,
    current: dict,
    filename: str,
    path: Path | None = None,
) -> bool:
    if not meta:
        return True
    if not meta.get("year"):
        return True
    if not meta.get("authors") and not (current or {}).get("authors"):
        return True
    if not meta.get("venue") and not (meta.get("doi") or meta.get("arxiv_id")):
        return True
    title = (current or {}).get("title") or ""
    if is_placeholder_title(title, filename) and not meta.get("title"):
        return True
    # 单位不是待补全状态的硬性条件，但本地文本层和在线源都没有读到
    # 单位时，单篇抓取仍应给模型一次读取首页的机会。没有 PDF 则不触发。
    if not meta.get("orgs") and path is not None and path.exists():
        return True
    return False


def _ai_extract(
    conn,
    path: Path | None,
    text: str,
    filename: str,
    title_hint: str,
    hints: dict,
    known: dict,
) -> tuple[dict, str, str]:
    """让已配置的模型读首页。有文本层给文本，扫描件才渲染图片。"""
    if path is None:
        raise RuntimeError("没有可读的 PDF 文件")
    from . import ai as ai_src

    if len(text) >= MIN_TEXT_CHARS:
        meta, model = ai_src.extract_metadata(
            conn,
            text=text[:MAX_PROMPT_CHARS],
            filename=filename,
            title_hint=title_hint,
            hints={**hints, **known},
        )
        return meta, model, "text"

    image = render_page(path)
    if not image:
        raise ai_src.NoContent("这个 PDF 抽不出文字，也渲染不了首页")
    meta, model = ai_src.extract_metadata(
        conn,
        image_png=image,
        filename=filename,
        title_hint=title_hint,
        hints={**hints, **known},
    )
    return meta, model, "image"


def resolve(
    filename: str,
    path: Path | None,
    title_hint: str = "",
    known_doi: str = "",
    known_arxiv: str = "",
    current: dict | None = None,
    conn=None,
    use_ai: bool = True,
) -> dict:
    """优先从本地 PDF 读取元数据，缺字段时再访问在线源。"""
    current = current or {}
    info = read_pdf_info(path)
    hints = _hints_from_info(info, filename)
    text = pdf_text(path) if path is not None and path.exists() else ""

    # 文本层是第一来源：论文首页通常直接包含标题、作者和单位。
    # 后续在线结果只通过 _merge_meta 补空，不覆盖本地已读到的内容。
    meta: dict = {"source": ""}
    local_meta = _local_text_meta(text, filename, title_hint) if text else {}
    if local_meta:
        meta.update(local_meta)
        meta["source"] = local_meta.get("source") or "PDF 文本层"

    # 已确认的数据库字段优先于本次自动识别；导入时的文件名占位标题不
    # 在 _protected 中，因此仍允许本地文本或在线源替换它。
    protected = _protected(current, filename)
    for key in ("title", "year", "venue", "doi", "arxiv_id", "authors", "orgs"):
        if protected.get(key) and current.get(key) not in (None, "", []):
            value = current[key]
            meta[key] = list(value) if key in ("authors", "orgs") else value

    hay = "\n".join(
        [filename or "", title_hint or "", " ".join(info.values()), text]
    )
    doi, arxiv = detect_identifiers(hay)
    doi = doi or (known_doi or "").strip()
    arxiv = arxiv or (known_arxiv or "").strip()
    if not doi and arxiv:
        doi = f"10.48550/arXiv.{arxiv}"
    patent = detect_patent(hay)

    errors: list[str] = []

    # 让后续的合并与补源逻辑可以安全地处理所有返回路径（包括没有命中
    # 在线记录、但后来由模型读出 DOI 的路径）。
    meta.setdefault("doi", doi)
    meta.setdefault("arxiv_id", arxiv)
    meta.setdefault("authors", [])
    meta.setdefault("orgs", [])

    # DocInfo 是本地文件的第二种来源，同样只补文本层没有读到的字段。
    added = _merge_meta(meta, hints, protected)
    if added:
        meta["source"] = _join_source(meta.get("source", ""), "PDF 信息")

    # 本地字段不足时才访问在线源；有 DOI / arXiv 时按标识查询，否则按
    # 本地标题（或文件名）反查 Crossref。在线结果只补空字段。
    needs_online = bool(missing_fields(meta) or not meta.get("orgs"))
    if needs_online:
        online_meta: dict | None = None
        if doi or arxiv or patent:
            online_meta, lookup_errors = _lookup_online(doi, arxiv, patent)
            errors.extend(lookup_errors)
        else:
            search_title = title_hint if not is_placeholder_title(title_hint, filename) else ""
            search_title = _clean(search_title) or _clean(meta.get("title") or "")
            search_title = search_title or _filename_title(filename)
            if search_title:
                try:
                    online_meta = search_crossref(search_title)
                except Exception as exc:
                    errors.append(f"Crossref 标题: {exc}")

        if online_meta:
            # ``meta`` 里只有本地已读到的值；空位应允许在线结果填入。
            # current 只代表数据库旧值，不能阻止本次对空位的补全。
            added = _merge_meta(meta, online_meta, {})
            meta["source"] = _join_source(
                meta.get("source", ""), online_meta.get("source", "")
            )
            if added:
                meta["source"] = _join_source(meta.get("source", ""), "在线补全")

        # 在线源经常没有 affiliation；此处再用 OpenAlex / Crossref 补单位。
        _enrich_online(meta, current, filename, errors)

    ai_used = False
    ai_mode = ""
    ai_model = ""
    ai_error = ""
    verified = False
    if use_ai and conn is not None and _needs_ai(meta, current, filename, path):
        ai_meta: dict = {}
        try:
            ai_meta, ai_model, ai_mode = _ai_extract(
                conn,
                path,
                text,
                filename,
                title_hint,
                hints,
                {"doi": doi, "arxiv_id": arxiv, "patent": patent},
            )
            ai_used = True
        except Exception as exc:
            ai_error = str(exc)

        if ai_meta:
            # 模型给的空话和垃圾字段先清掉，再谈合并
            ai_meta["title"] = _usable_title(ai_meta.get("title") or "")
            ai_meta["venue"] = _usable_venue(ai_meta.get("venue") or "")

            # 只认规则校验后仍然可信的编号；模型读错一位 DOI 比留空更糟
            model_doi, model_arxiv = detect_identifiers(
                " ".join(
                    [ai_meta.get("doi") or "", ai_meta.get("arxiv_id") or ""]
                )
            )
            model_patent = detect_patent(ai_meta.get("patent") or "")
            authoritative = None
            if model_arxiv and not arxiv:
                try:
                    authoritative = fetch_arxiv(model_arxiv)
                except Exception:
                    authoritative = None
            if authoritative is None and model_doi and not doi and not _protected(current, filename)["doi"]:
                try:
                    authoritative = fetch_crossref(model_doi)
                except Exception:
                    authoritative = None
            if authoritative is None and model_patent and not patent:
                authoritative = _patent_meta(model_patent)

            if authoritative:
                verified = True
                _merge_meta(meta, authoritative, _protected(current, filename))
                meta["source"] = _join_source(
                    meta.get("source", ""), authoritative.get("source", "")
                )
                meta["source"] = _join_source(meta["source"], "回源核对")
            else:
                ai_meta.pop("doi", None)
                ai_meta.pop("arxiv_id", None)
                ai_meta.pop("patent", None)

            # 文本阶段才校验年份有没有在首页文本里出现过，识图阶段只能信模型 + 范围检查
            if ai_mode == "text" and ai_meta.get("year"):
                years = {int(y) for y in YEAR_RE.findall(text)}
                if years and ai_meta["year"] not in years:
                    ai_meta["year"] = None

            added = _merge_meta(meta, ai_meta, _protected(current, filename))
            if added:
                meta["source"] = _join_source(
                    meta.get("source", ""),
                    "模型读首页",
                )

            # 模型新识别出的 DOI 也要走同一条单位补全链路；否则这类文献
            # 只会得到标题 / 作者 / 年份，单位仍然是空的。
            _enrich_online(meta, current, filename, errors)

    missing = missing_fields(meta)
    needs_check = ai_used and bool(
        meta.get("source") and "模型读首页" in meta.get("source", "")
    )

    if not meta.get("title") and not meta.get("authors") and not meta.get("year"):
        status = "no-id"
        if not (doi or arxiv or patent):
            if path is None or not path.exists():
                message = "这条还没有 PDF，只能手动补"
            elif len(text) < MIN_TEXT_CHARS:
                message = "首页是扫描图，没能识别出编号"
                if ai_error:
                    message += "；模型也没读成功：" + ai_error
                elif not ai_used:
                    message += "；配置模型后可让它读首页"
            else:
                message = "文件里没找到 DOI / arXiv / 专利号"
                if ai_error:
                    message += "；模型也没读成功：" + ai_error
            return {
                "status": status,
                "doi": doi,
                "arxiv_id": arxiv,
                "patent": patent,
                "message": message,
                "ai": {"used": ai_used, "error": ai_error},
            }
        status = "not-found"
        message = "在线源里没有这条记录" + ("（" + errors[-1] + "）" if errors else "")
    else:
        status = "ok"
        label = "模型读了首页图片" if ai_mode == "image" else "模型读了首页文本"
        if ai_used:
            if missing:
                message = f"{label}，仍缺：" + "、".join(missing)
            else:
                message = f"{label}，已补全，请核对"
            if verified:
                message = message.replace("，请核对", "，编号已回源核对，请核对其余字段")
        elif missing:
            message = "抓到部分字段，仍缺：" + "、".join(missing)
        else:
            message = "已补全"
        if ai_error and not ai_used:
            message += "；模型没读成功：" + ai_error

    return {
        "status": status,
        "source": meta.get("source", ""),
        "meta": meta,
        "missing": missing,
        "patent": patent,
        "message": message,
        "needsCheck": needs_check,
        "ai": {"used": ai_used, "mode": ai_mode, "model": ai_model, "error": ai_error},
    }
