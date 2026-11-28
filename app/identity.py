from __future__ import annotations

import re
import sqlite3

from .metadata import DOI_RE, pdf_text, read_pdf_info

ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?"
ARXIV_LABEL = re.compile(rf"\b(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)({ARXIV_ID})\b", re.I)
DOI_LABEL = re.compile(r"(?:^\s*|\bdoi\s*:?\s*|https?://(?:dx\.)?doi\.org/)(" + DOI_RE.pattern + r")", re.I | re.M)


def doi_key(value: str | None) -> str:
    value = re.sub(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", "", (value or "").strip(), flags=re.I)
    return value.lower() if DOI_RE.fullmatch(value.lower()) else ""


def arxiv_key(value: str | None) -> str:
    value = re.sub(r"^(?:arxiv\s*:\s*|https?://arxiv\.org/(?:abs|pdf)/)", "", (value or "").strip(), flags=re.I)
    value = re.sub(r"\.pdf$", "", value, flags=re.I).lower()
    return re.sub(r"v\d+$", "", value) if re.fullmatch(ARXIV_ID, value, re.I) else ""


def pdf_keys(data: bytes) -> tuple[set[str], set[str]]:
    info = read_pdf_info(data)
    # Use the header before the abstract, not DOI references in the paper body.
    head = pdf_text(data, max_pages=1)
    head = re.split(r"(?im)^\s*(?:abstract\b|摘\s*要|references\b|参考文献)", head, maxsplit=1)[0]
    head = "\n".join(head.splitlines()[:30])
    embedded = doi_key(info.get("doi"))
    dois = {embedded} if embedded else {m.group(1).lower() for m in DOI_LABEL.finditer(head)}
    arxiv = {arxiv_key(m.group(1)) for m in ARXIV_LABEL.finditer(head)}
    if embedded_arxiv := arxiv_key(info.get("arxiv") or info.get("arxiv_id")):
        arxiv.add(embedded_arxiv)
    return dois, arxiv


def bibliographic_matches(
    conn: sqlite3.Connection, dois: set[str], arxiv: set[str]
) -> set[str]:
    if not dois and not arxiv:
        return set()
    return {
        row["id"] for row in conn.execute(
            "SELECT id, doi, arxiv_id FROM item WHERE deleted_at IS NULL "
            "AND (doi IS NOT NULL OR arxiv_id IS NOT NULL)"
        ) if doi_key(row["doi"]) in dois or arxiv_key(row["arxiv_id"]) in arxiv
    }
