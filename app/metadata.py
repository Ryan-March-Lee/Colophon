from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

UA = "Colophon/0.1 (local literature manager)"
TIMEOUT = 8

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"(?:arxiv[:/\s]|abs/)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
ARXIV_BARE_RE = re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


# ---------------------------------------------------------------- 识别

def detect_identifiers(text: str) -> tuple[str, str]:
    """返回 (doi, arxiv_id)。先看图里的 DOI，再找 arXiv 编号。"""
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


def pdf_text(path: Path, max_pages: int = 2) -> str:
    """抽前几页文本，只用于找 DOI / arXiv 编号。失败就当没有。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        chunks = []
        for page in reader.pages[:max_pages]:
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks)
    except Exception:
        return ""


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


def fetch_crossref(doi: str) -> dict | None:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")
    data = json.loads(_get(url))
    msg = data.get("message") or {}
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


def resolve(
    filename: str,
    path: Path | None,
    title_hint: str = "",
    known_doi: str = "",
    known_arxiv: str = "",
) -> dict:
    """从文件名和 PDF 前几页找标识，再去在线源补全。"""
    hay = "\n".join([filename or "", title_hint or ""])
    if path is not None and path.exists():
        hay += "\n" + pdf_text(path)
    doi, arxiv = detect_identifiers(hay)
    doi = doi or (known_doi or "").strip()
    arxiv = arxiv or (known_arxiv or "").strip()
    if not doi and arxiv:
        doi = f"10.48550/arXiv.{arxiv}"

    if not (doi or arxiv):
        return {
            "status": "no-id",
            "message": "文件里没找到 DOI 或 arXiv 编号，只能手动补",
        }

    meta = None
    errors = []
    if arxiv:
        try:
            meta = fetch_arxiv(arxiv)
        except Exception as exc:  # 网络 / 解析失败都退回 DOI
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

    if meta is None:
        return {
            "status": "not-found",
            "doi": doi,
            "arxiv_id": arxiv,
            "message": "在线源里没有这条记录" + ("（" + errors[-1] + "）" if errors else ""),
        }

    # 用 OpenAlex 补作者单位、摘要这类 Crossref 不给的字段
    if meta.get("doi"):
        try:
            extra = fetch_openalex(meta["doi"])
            if extra:
                if not meta.get("orgs"):
                    meta["orgs"] = extra.get("orgs") or []
                if not meta.get("abstract"):
                    meta["abstract"] = extra.get("abstract") or ""
                if not meta.get("year"):
                    meta["year"] = extra.get("year")
                if not meta.get("venue"):
                    meta["venue"] = extra.get("venue")
                if extra.get("source") and extra["source"] not in meta["source"]:
                    meta["source"] = meta["source"] + " + " + extra["source"]
        except Exception:
            pass

    meta.setdefault("doi", doi)
    meta.setdefault("arxiv_id", arxiv)
    missing = missing_fields(meta)
    return {
        "status": "ok",
        "source": meta.get("source", ""),
        "meta": meta,
        "missing": missing,
        "message": "已补全" if not missing else "抓到部分字段，仍缺：" + "、".join(missing),
    }
