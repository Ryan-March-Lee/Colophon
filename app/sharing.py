from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import repo
from .db import new_id, now
from .identity import arxiv_key, doi_key

FORMAT = "colophon-library-share"
SUFFIX = ".colophon.json"
MAX_BYTES = 32 * 1024 * 1024
MAX_ITEMS = 10000
Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")]
Name = Annotated[str, Field(min_length=1, max_length=120, pattern=r"\S")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
META_FIELDS = ("type", "title", "abstract", "year", "venue", "doi", "arxiv_id", "url", "language", "cites")


class ShareModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ShareGroup(ShareModel):
    id: Key
    name: Name
    color: str = ""


class ShareTag(ShareGroup):
    definition: str = Field(default="", max_length=120)
    group_id: Key | None = None


class ShareItem(ShareModel):
    id: Key
    type: str = "article"
    title: str
    abstract: str = ""
    year: int | None = Field(default=None, ge=-(2**63), le=2**63-1)
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    url: str | None = None
    language: str = "en"
    cites: int = Field(default=0, ge=-(2**63), le=2**63-1)
    authors: list[str] = Field(default_factory=list)
    orgs: list[str] = Field(default_factory=list)
    tag_ids: list[Key] = Field(default_factory=list)
    pdf_hashes: list[Digest] = Field(default_factory=list)
    note: str | None = None


class SharePackage(ShareModel):
    format: Literal["colophon-library-share"]
    version: Literal[1]
    items: list[ShareItem] = Field(min_length=1, max_length=MAX_ITEMS)
    tags: list[ShareTag]
    groups: list[ShareGroup] = Field(default_factory=list)


def _public_url(value: str | None) -> str | None:
    try:
        url = urlsplit(value or "")
        if url.scheme in ("http", "https") and url.hostname and not (url.username or url.password):
            return value
    except ValueError:
        pass
    return None


def _color(value: str) -> str:
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value or "") else ""


def export_package(conn: sqlite3.Connection, ids: list[str], groups: bool, notes: bool) -> bytes:
    ids = list(dict.fromkeys(ids))
    rows = {row["id"]: row for row in conn.execute("SELECT * FROM item WHERE deleted_at IS NULL")}
    if any(item_id not in rows for item_id in ids):
        raise ValueError("部分选中文献已不存在，请刷新书库后重试")
    papers = repo._rows_to_items(conn, [rows[item_id] for item_id in ids])
    tags_used = {tag_id for paper in papers for tag_id in paper["topicIds"]}
    tags = []
    groups_used = set()
    for row in conn.execute(
        "SELECT t.id, t.name, t.color, t.definition, g.id AS group_id "
        "FROM tag t LEFT JOIN tag_group_member m ON m.tag_id = t.id "
        "LEFT JOIN tag_group g ON g.id = m.group_id AND g.deleted_at IS NULL "
        "WHERE t.deleted_at IS NULL ORDER BY g.position, m.position, t.name"
    ):
        if row["id"] in tags_used:
            tag = dict(row)
            if groups and tag["group_id"]:
                groups_used.add(tag["group_id"])
            else:
                del tag["group_id"]
            tags.append(tag)
    payload = {"format": FORMAT, "version": 1, "items": [], "tags": tags}
    if groups_used:
        payload["groups"] = [dict(row) for row in conn.execute(
            "SELECT id, name, color FROM tag_group WHERE deleted_at IS NULL ORDER BY position, rowid"
        ) if row["id"] in groups_used]
    for paper in papers:
        row = rows[paper["id"]]
        item_id = paper["id"]
        origin = conn.execute(
            "SELECT share_id FROM shared_item_key WHERE item_id = ? ORDER BY rowid LIMIT 1", (item_id,)
        ).fetchone()
        hashes = conn.execute(
            "SELECT sha256 FROM file WHERE item_id = ? "
            "UNION SELECT sha256 FROM shared_pdf_hash WHERE item_id = ? ORDER BY sha256", (item_id, item_id)
        )
        record = {field: row[field] for field in META_FIELDS}
        record.update(id=origin["share_id"] if origin else item_id, authors=paper["authors"],
                      orgs=paper["tags"]["org"], url=_public_url(row["url"]),
                      tag_ids=paper["topicIds"], pdf_hashes=[h["sha256"] for h in hashes])
        if notes:
            record["note"] = row["note_md"]
        payload["items"].append(record)
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("分享包超过 32 MB，请减少选择的文献或不包含个人笔记")
    return data


def read_package(data: bytes) -> SharePackage:
    if len(data) > MAX_BYTES:
        raise ValueError("分享包不能超过 32 MB")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError) as exc:
        raise ValueError("无法读取分享包：需要有效的 UTF-8 JSON 文件") from exc
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ValueError("所选文件不是 Colophon 文献分享包")
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("此版本的 Colophon 不支持该分享包格式版本")
    try:
        package = SharePackage.model_validate(raw)
    except ValidationError as exc:
        raise ValueError("分享包的字段不完整或格式不正确") from exc
    tags = {tag.id for tag in package.tags}
    groups = {group.id for group in package.groups}
    if (len(tags) != len(package.tags) or len(groups) != len(package.groups)
            or len({item.id for item in package.items}) != len(package.items)
            or any(tag.group_id is not None and tag.group_id not in groups for tag in package.tags)
            or any(set(item.tag_ids) - tags for item in package.items)):
        raise ValueError("分享包包含重复标识或无效的标签分组关联")
    return package


class ImportMatches:
    def __init__(self, conn: sqlite3.Connection):
        self.items = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM item WHERE deleted_at IS NULL")}
        self.keys: dict[str, str] = {}
        self.hashes: dict[str, set[str]] = {}
        self.dois: dict[str, set[str]] = {}
        self.arxiv: dict[str, set[str]] = {}
        for row in self.items.values():
            self.add_item(row)
        for row in conn.execute("SELECT share_id, item_id FROM shared_item_key"):
            if row["item_id"] in self.items:
                self.keys[row["share_id"]] = row["item_id"]
        for row in conn.execute("SELECT sha256, item_id FROM file UNION SELECT sha256, item_id FROM shared_pdf_hash"):
            if row["item_id"] in self.items:
                self.hashes.setdefault(row["sha256"], set()).add(row["item_id"])

    def add_item(self, row: dict) -> None:
        item_id = row["id"]
        self.items[item_id] = row
        if doi := doi_key(row["doi"]):
            self.dois.setdefault(doi, set()).add(item_id)
        if arxiv := arxiv_key(row["arxiv_id"]):
            self.arxiv.setdefault(arxiv, set()).add(item_id)

    def find(self, item: ShareItem) -> tuple[str | None, str]:
        doi, arxiv = doi_key(item.doi), arxiv_key(item.arxiv_id)
        candidates = set(self.dois.get(doi, ())) | set(self.arxiv.get(arxiv, ()))
        for digest in item.pdf_hashes:
            candidates.update(self.hashes.get(digest, ()))
        candidates.update(key for key in (self.keys.get(item.id), item.id) if key in self.items)
        if len(candidates) > 1:
            return None, "标识指向不同的本地文献"
        if candidates:
            item_id = candidates.pop()
            row = self.items[item_id]
            local_doi, local_arxiv = doi_key(row["doi"]), arxiv_key(row["arxiv_id"])
            if (doi and local_doi and doi != local_doi) or (arxiv and local_arxiv and arxiv != local_arxiv):
                return None, "分享包与本地文献的 DOI 或 arXiv 编号不一致"
            return item_id, ""
        return None, ""


def _save_item(conn: sqlite3.Connection, item: ShareItem, item_id: str, local: dict | None) -> dict:
    meta = item.model_dump(include=set(META_FIELDS))
    meta.update(url=_public_url(item.url), note_md=item.note or "")
    timestamp = now()
    if local is None:
        row = dict(meta, id=item_id, added_at=timestamp, updated_at=timestamp)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO item ({cols}) VALUES ({marks})", tuple(row.values()))
        authors, orgs = item.authors, item.orgs
    else:
        changes = {key: value for key, value in meta.items() if value and not local[key]}
        file = repo.get_item_file(conn, item_id)
        if (file and local["needs_review"] and item.title.strip()
                and local["title"] == Path(file["name"]).stem):
            changes["title"] = item.title
        if changes:
            changes["updated_at"] = timestamp
            sets = ", ".join(f"{key} = ?" for key in changes)
            conn.execute(f"UPDATE item SET {sets} WHERE id = ?", (*changes.values(), item_id))
        if "abstract" in changes or "title" in changes:
            conn.execute("DELETE FROM passage WHERE item_id = ? AND kind = 'abstract'", (item_id,))
        state = repo.metadata_state(conn, item_id)
        authors = [] if state["authors"] else item.authors
        orgs = [] if state["orgs"] else item.orgs
        row = {**local, **changes}
    repo._replace_people(conn, item_id, authors, orgs)
    if local is None or local["needs_review"]:
        repo._recompute_review(conn, item_id)
    return row


def _save_tags(conn: sqlite3.Connection, package: SharePackage, used: set[str]) -> dict[str, str]:
    tags = [tag for tag in package.tags if tag.id in used]
    ids = {tag.id: repo._find_tag(conn, tag.name.strip()) for tag in tags}
    needed_groups = {tag.group_id for tag in tags if not ids[tag.id]}
    groups = {group.id: repo.ensure_tag_group(conn, group.name, _color(group.color))
              for group in package.groups if group.id in needed_groups}
    for tag in tags:
        if ids[tag.id]:
            continue
        name = tag.name.strip()
        existing = repo._find_tag(conn, name)
        tag_id = existing or repo.ensure_tag(conn, name, groups.get(tag.group_id))
        if not existing:
            conn.execute("UPDATE tag SET definition = ?, color = COALESCE(NULLIF(?, ''), color) WHERE id = ?",
                         (tag.definition, _color(tag.color), tag_id))
        ids[tag.id] = tag_id
    return ids


def import_package(conn: sqlite3.Connection, data: bytes, expected: str | None = None) -> dict:
    package = read_package(data)
    preview = expected is None
    outcomes = []
    records = []
    new_items = {}
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("SAVEPOINT share_import")
        matches = ImportMatches(conn)
        for item in package.items:
            local_id, conflict = matches.find(item)
            if not conflict:
                item_id = local_id or new_id("itm")
                # An invalid legacy identifier may not normalize but can still violate a unique constraint.
                conn.execute("SAVEPOINT share_item")
                try:
                    row = _save_item(conn, item, item_id, matches.items.get(item_id))
                    conn.execute("INSERT OR IGNORE INTO shared_item_key (share_id, item_id) VALUES (?, ?)",
                                 (item.id, item_id))
                except sqlite3.IntegrityError:
                    conn.execute("ROLLBACK TO share_item")
                    conflict = "文献标识与现有记录冲突"
                conn.execute("RELEASE share_item")
            if conflict:
                outcomes.append((item.id, "conflict", conflict))
                records.append({"title": item.title, "status": "conflict", "reason": conflict})
                continue
            matches.add_item(row)
            matches.keys[item.id] = item_id
            for digest in item.pdf_hashes:
                matches.hashes.setdefault(digest, set()).add(item_id)
                conn.execute("INSERT OR IGNORE INTO shared_pdf_hash (sha256, item_id) VALUES (?, ?)", (digest, item_id))
            status = "merged" if local_id else "added"
            if not local_id:
                new_items[item_id] = item.id
            target = ("new", new_items[item_id]) if item_id in new_items else ("local", item_id)
            outcomes.append((item.id, status, target))
            records.append({"title": item.title, "status": status, "item_id": item_id, "tag_ids": item.tag_ids})
        token = hashlib.sha256(data + json.dumps(outcomes).encode("utf-8")).hexdigest()
        if not preview and token != expected:
            raise ValueError("分享包或书库匹配结果已改变，请重新预览")
        if preview:
            conn.execute("ROLLBACK TO share_import")
        else:
            tags = _save_tags(conn, package, {tag for rec in records for tag in rec.get("tag_ids", ())})
            conn.executemany(
                "INSERT OR IGNORE INTO item_tag (item_id, tag_id, created_at) VALUES (?, ?, ?)",
                ((rec["item_id"], tags[tag_id], now()) for rec in records for tag_id in rec.get("tag_ids", ())),
            )
        conn.execute("RELEASE share_import")
    return {"token": token, "added": sum(r["status"] == "added" for r in records),
            "merged": sum(r["status"] == "merged" for r in records),
            "conflicts": [{"title": r["title"], "reason": r["reason"]} for r in records if r["status"] == "conflict"],
            "hasNotes": any(bool(item.note) for item in package.items)}
