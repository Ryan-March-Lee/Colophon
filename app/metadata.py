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
            return "\n".join(chunks)
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
        "orgs": [],
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
    if not base.get("orgs"):
        orgs = [o for o in (extra.get("orgs") or []) if _clean(o)]
        if orgs:
            base["orgs"] = orgs
            added.append("orgs")
    return added


def _join_source(current: str, extra: str) -> str:
    parts = [p.strip() for p in (current or "").split("+") if p.strip()]
    if extra and extra not in parts:
        parts.append(extra)
    return " + ".join(parts)


def _needs_ai(meta: dict, current: dict, filename: str) -> bool:
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
    """从内嵌信息、文件名和 PDF 首页补全元数据。

    顺序：内嵌 DocInfo → DOI/arXiv/专利号回源 → 标题反查 Crossref →
    模型读首页文本 → 扫描件渲染首页识图 → 模型读到的编号再回源核对。
    """
    current = current or {}
    info = read_pdf_info(path)
    hints = _hints_from_info(info, filename)
    text = pdf_text(path) if path is not None and path.exists() else ""

    hay = "\n".join(
        [filename or "", title_hint or "", " ".join(info.values()), text]
    )
    doi, arxiv = detect_identifiers(hay)
    doi = doi or (known_doi or "").strip()
    arxiv = arxiv or (known_arxiv or "").strip()
    if not doi and arxiv:
        doi = f"10.48550/arXiv.{arxiv}"
    patent = detect_patent(hay)

    meta: dict | None = None
    if doi or arxiv or patent:
        meta, errors = _lookup_online(doi, arxiv, patent)
    else:
        errors = []

    # 标题反查只在完全没有编号时试一次，且要求高相似度
    if meta is None and not (doi or arxiv or patent):
        search_title = title_hint if not is_placeholder_title(title_hint, filename) else ""
        search_title = _clean(search_title) or _filename_title(filename)
        try:
            meta = search_crossref(search_title)
        except Exception as exc:
            errors.append(f"Crossref 标题: {exc}")

    if meta is None:
        meta = {"source": ""}

    # PDF 自带的信息只能补空位；它可能是出版社或扫描工具写的，未必可靠
    added = _merge_meta(meta, hints, _protected(current, filename))
    if added:
        meta["source"] = _join_source(meta.get("source", ""), "PDF 信息")

    ai_used = False
    ai_mode = ""
    ai_model = ""
    ai_error = ""
    verified = False
    if use_ai and conn is not None and _needs_ai(meta, current, filename):
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
