from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from .db import LIBRARY, new_id, now

TAG_COLORS = ["#1c7566", "#a2672c", "#7757a6", "#5b6f83", "#8a5a22", "#2f7d4f"]


# ---------------------------------------------------------------- 读取

def _row_to_item(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item_id = row["id"]
    authors = [
        r["display_name"]
        for r in conn.execute(
            "SELECT a.display_name FROM item_author ia "
            "JOIN author a ON a.id = ia.author_id "
            "WHERE ia.item_id = ? ORDER BY ia.position, a.display_name",
            (item_id,),
        )
    ]
    orgs = [
        r["name"]
        for r in conn.execute(
            "SELECT o.name FROM item_org io JOIN org o ON o.id = io.org_id "
            "WHERE io.item_id = ? ORDER BY io.position, o.name",
            (item_id,),
        )
    ]
    topics = [
        r["name"]
        for r in conn.execute(
            "SELECT t.name FROM item_tag it JOIN tag t ON t.id = it.tag_id "
            "WHERE it.item_id = ? AND t.deleted_at IS NULL "
            "ORDER BY it.created_at, t.name",
            (item_id,),
        )
    ]
    file_row = conn.execute(
        "SELECT original_name FROM file WHERE item_id = ? ORDER BY added_at LIMIT 1",
        (item_id,),
    ).fetchone()
    missing = []
    if not row["title"]:
        missing.append("title")
    if not authors:
        missing.append("authors")
    if not row["year"]:
        missing.append("year")
    if not (row["venue"] or row["doi"] or row["arxiv_id"]):
        missing.append("venue")
    return {
        "id": item_id,
        "title": row["title"],
        "authors": authors,
        "venue": row["venue"] or "",
        "doi": row["doi"] or "",
        "arxivId": row["arxiv_id"] or "",
        "url": row["url"] or "",
        "cites": row["cites"],
        "status": row["status"],
        "abstract": row["abstract"],
        "aiBrief": row["ai_brief"],
        "memory": row["memory"],
        "note": row["note_md"],
        "needsReview": bool(row["needs_review"]),
        "hasFile": bool(file_row),
        "fileName": file_row["original_name"] if file_row else "",
        "missing": missing,
        "tags": {
            "year": [str(row["year"])] if row["year"] else [],
            "author": authors,
            "org": orgs,
            "topic": topics,
        },
    }


def list_items(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM item WHERE deleted_at IS NULL ORDER BY rowid"
    ).fetchall()
    return [_row_to_item(conn, row) for row in rows]


def get_item(conn: sqlite3.Connection, item_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM item WHERE id = ? AND deleted_at IS NULL", (item_id,)
    ).fetchone()
    return _row_to_item(conn, row) if row else None


def import_queue(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM item WHERE needs_review = 1 AND deleted_at IS NULL ORDER BY rowid"
    ).fetchall()
    return [_row_to_item(conn, row) for row in rows]


def metadata_state(conn: sqlite3.Connection, item_id: str) -> dict | None:
    """当前元数据快照，给「只填空、不覆盖」的判断用。"""
    row = conn.execute(
        "SELECT title, year, venue, doi, arxiv_id, needs_review FROM item "
        "WHERE id = ? AND deleted_at IS NULL",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    authors = [
        r["display_name"]
        for r in conn.execute(
            "SELECT a.display_name FROM item_author ia "
            "JOIN author a ON a.id = ia.author_id "
            "WHERE ia.item_id = ? ORDER BY ia.position",
            (item_id,),
        )
    ]
    return {
        "title": row["title"] or "",
        "year": row["year"],
        "venue": row["venue"] or "",
        "doi": row["doi"] or "",
        "arxiv_id": row["arxiv_id"] or "",
        "authors": authors,
        "needs_review": bool(row["needs_review"]),
    }


def list_tags(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT t.id, t.name, t.color, t.definition, COUNT(it.item_id) AS count "
        "FROM tag t "
        "LEFT JOIN item_tag it ON it.tag_id = t.id "
        "LEFT JOIN item i ON i.id = it.item_id AND i.deleted_at IS NULL "
        "WHERE t.deleted_at IS NULL "
        "GROUP BY t.id ORDER BY count DESC, t.name"
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------- 实体

def _find_tag(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT tag_id FROM tag_alias WHERE alias = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return row["tag_id"]
    row = conn.execute(
        "SELECT id, deleted_at FROM tag WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row and not row["deleted_at"]:
        return row["id"]
    return None


def ensure_tag(conn: sqlite3.Connection, name: str) -> str:
    name = name.strip()
    row = conn.execute(
        "SELECT tag_id FROM tag_alias WHERE alias = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return row["tag_id"]
    row = conn.execute(
        "SELECT id, deleted_at FROM tag WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        if row["deleted_at"]:
            conn.execute("UPDATE tag SET deleted_at = NULL WHERE id = ?", (row["id"],))
        return row["id"]
    tag_id = new_id("tg")
    color = TAG_COLORS[sum(ord(c) for c in name) % len(TAG_COLORS)]
    conn.execute(
        "INSERT INTO tag (id, name, color, created_at) VALUES (?, ?, ?, ?)",
        (tag_id, name, color, now()),
    )
    return tag_id


def ensure_author(conn: sqlite3.Connection, name: str) -> str:
    name = name.strip()
    row = conn.execute(
        "SELECT id FROM author WHERE display_name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return row["id"]
    author_id = new_id("au")
    conn.execute(
        "INSERT INTO author (id, display_name, sort_name) VALUES (?, ?, ?)",
        (author_id, name, name),
    )
    return author_id


def ensure_org(conn: sqlite3.Connection, name: str) -> str:
    name = name.strip()
    row = conn.execute(
        "SELECT id FROM org WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return row["id"]
    org_id = new_id("or")
    conn.execute(
        "INSERT INTO org (id, name, normalized_name) VALUES (?, ?, ?)",
        (org_id, name, name.lower()),
    )
    return org_id


# ---------------------------------------------------------------- 写入

def patch_item(conn: sqlite3.Connection, item_id: str, fields: dict) -> dict | None:
    allowed = {"status", "starred", "memory", "note_md"}
    sets: list[str] = []
    values: list = []
    for key, value in fields.items():
        if key in allowed:
            sets.append(f"{key} = ?")
            values.append(value)
    if sets:
        sets.append("updated_at = ?")
        values.append(now())
        values.append(item_id)
        conn.execute(
            f"UPDATE item SET {', '.join(sets)} WHERE id = ? AND deleted_at IS NULL",
            values,
        )
        conn.commit()
    return get_item(conn, item_id)


def _recompute_review(conn: sqlite3.Connection, item_id: str) -> list[str]:
    row = conn.execute("SELECT * FROM item WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return []
    has_author = conn.execute(
        "SELECT 1 FROM item_author WHERE item_id = ? LIMIT 1", (item_id,)
    ).fetchone()
    missing = []
    if not row["title"]:
        missing.append("title")
    if not has_author:
        missing.append("authors")
    if not row["year"]:
        missing.append("year")
    if not (row["venue"] or row["doi"] or row["arxiv_id"]):
        missing.append("venue")
    conn.execute(
        "UPDATE item SET needs_review = ? WHERE id = ?",
        (1 if missing else 0, item_id),
    )
    return missing


def _replace_people(conn: sqlite3.Connection, item_id: str, authors: list[str], orgs) -> None:
    names = [a.strip() for a in authors if a and a.strip()]
    if names:
        conn.execute("DELETE FROM item_author WHERE item_id = ?", (item_id,))
        for position, name in enumerate(names):
            author_id = ensure_author(conn, name)
            conn.execute(
                "INSERT OR IGNORE INTO item_author (item_id, author_id, position) "
                "VALUES (?, ?, ?)",
                (item_id, author_id, position),
            )

    if orgs:
        conn.execute("DELETE FROM item_org WHERE item_id = ?", (item_id,))
        for position, name in enumerate(o for o in orgs if o and o.strip()):
            org_id = ensure_org(conn, name.strip())
            conn.execute(
                "INSERT OR IGNORE INTO item_org (item_id, org_id, position) VALUES (?, ?, ?)",
                (item_id, org_id, position),
            )


def apply_metadata(
    conn: sqlite3.Connection,
    item_id: str,
    meta: dict,
    keep_review: bool = False,
) -> dict | None:
    """把抓到的元数据写回；作者 / 单位是替换，其余字段只在有新值时覆盖。

    keep_review=True 用于模型推断的结果：即使字段齐全也保留「待补全」，
    等用户核对或手动保存后再清掉。
    """
    row = conn.execute(
        "SELECT * FROM item WHERE id = ? AND deleted_at IS NULL", (item_id,)
    ).fetchone()
    if row is None:
        return None

    def pick(key: str, current):
        value = meta.get(key)
        if isinstance(value, str):
            return value.strip() or current
        return value if value not in (None, "") else current

    doi = (meta.get("doi") or "").strip()
    if doi:
        owner = conn.execute(
            "SELECT id FROM item WHERE doi = ? AND id <> ?", (doi, item_id)
        ).fetchone()
        if owner:
            doi = row["doi"] or ""
            meta["conflict"] = owner["id"]

    arxiv = (meta.get("arxiv_id") or "").strip()
    if arxiv:
        owner = conn.execute(
            "SELECT id FROM item WHERE arxiv_id = ? AND id <> ?", (arxiv, item_id)
        ).fetchone()
        if owner:
            arxiv = row["arxiv_id"] or ""
            meta["conflict"] = owner["id"]

    conn.execute(
        "UPDATE item SET title = ?, abstract = ?, year = ?, venue = ?, doi = ?, "
        "arxiv_id = ?, url = ?, updated_at = ? WHERE id = ?",
        (
            pick("title", row["title"]),
            pick("abstract", row["abstract"]),
            meta.get("year") or row["year"],
            pick("venue", row["venue"]),
            doi or row["doi"],
            arxiv or row["arxiv_id"],
            pick("url", row["url"]),
            now(),
            item_id,
        ),
    )

    _replace_people(conn, item_id, meta.get("authors") or [], meta.get("orgs") or [])
    conn.execute("DELETE FROM passage WHERE item_id = ?", (item_id,))
    _recompute_review(conn, item_id)
    if keep_review:
        conn.execute("UPDATE item SET needs_review = 1 WHERE id = ?", (item_id,))
    conn.commit()
    return get_item(conn, item_id)


def patch_fields(conn: sqlite3.Connection, item_id: str, fields: dict) -> dict | None:
    """手动补全元数据。"""
    if not conn.execute(
        "SELECT 1 FROM item WHERE id = ? AND deleted_at IS NULL", (item_id,)
    ).fetchone():
        return None

    scalars = {"title", "abstract", "venue", "doi", "arxiv_id", "url"}
    sets: list[str] = []
    values: list = []
    for key, value in fields.items():
        if key in scalars and value is not None:
            sets.append(f"{key} = ?")
            values.append(str(value).strip())

    if "year" in fields:
        year = fields.get("year")
        sets.append("year = ?")
        values.append(int(year) if year not in (None, "") else None)

    if fields.get("doi"):
        owner = conn.execute(
            "SELECT id FROM item WHERE doi = ? AND id <> ?",
            (str(fields["doi"]).strip(), item_id),
        ).fetchone()
        if owner:
            raise ValueError("这个 DOI 已经在另一条文献上")

    if sets:
        sets.append("updated_at = ?")
        values.append(now())
        values.append(item_id)
        conn.execute(
            f"UPDATE item SET {', '.join(sets)} WHERE id = ?", values
        )

    if isinstance(fields.get("authors"), list):
        _replace_people(conn, item_id, fields["authors"], None)

    if any(key in fields for key in ("title", "abstract")):
        conn.execute(
            "DELETE FROM passage WHERE item_id = ? AND kind = 'abstract'", (item_id,)
        )

    _recompute_review(conn, item_id)
    conn.commit()
    return get_item(conn, item_id)


def add_tag(conn: sqlite3.Connection, item_id: str, facet: str, name: str) -> dict | None:
    if not conn.execute(
        "SELECT 1 FROM item WHERE id = ? AND deleted_at IS NULL", (item_id,)
    ).fetchone():
        return None
    name = name.strip()
    if not name:
        raise ValueError("empty name")

    if facet == "year":
        try:
            year = int(name)
        except ValueError as exc:
            raise ValueError("year must be an integer") from exc
        conn.execute(
            "UPDATE item SET year = ?, updated_at = ? WHERE id = ?",
            (year, now(), item_id),
        )
    elif facet == "topic":
        tag_id = ensure_tag(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO item_tag (item_id, tag_id, source, created_at) "
            "VALUES (?, ?, 'user', ?)",
            (item_id, tag_id, now()),
        )
    elif facet == "author":
        author_id = ensure_author(conn, name)
        pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM item_author WHERE item_id = ?",
            (item_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO item_author (item_id, author_id, position) "
            "VALUES (?, ?, ?)",
            (item_id, author_id, pos),
        )
    elif facet == "org":
        org_id = ensure_org(conn, name)
        pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM item_org WHERE item_id = ?",
            (item_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO item_org (item_id, org_id, position) VALUES (?, ?, ?)",
            (item_id, org_id, pos),
        )
    else:
        raise ValueError("unknown facet")

    conn.commit()
    return get_item(conn, item_id)


def remove_tag(conn: sqlite3.Connection, item_id: str, facet: str, name: str) -> dict | None:
    if facet == "year":
        try:
            year = int(name)
        except ValueError:
            year = None
        conn.execute(
            "UPDATE item SET year = NULL, updated_at = ? WHERE id = ? AND year = ?",
            (now(), item_id, year),
        )
    elif facet == "topic":
        tag_id = _find_tag(conn, name)
        if tag_id:
            conn.execute(
                "DELETE FROM item_tag WHERE item_id = ? AND tag_id = ?",
                (item_id, tag_id),
            )
    elif facet == "author":
        row = conn.execute(
            "SELECT id FROM author WHERE display_name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM item_author WHERE item_id = ? AND author_id = ?",
                (item_id, row["id"]),
            )
    elif facet == "org":
        row = conn.execute(
            "SELECT id FROM org WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM item_org WHERE item_id = ? AND org_id = ?",
                (item_id, row["id"]),
            )
    else:
        raise ValueError("unknown facet")

    conn.commit()
    return get_item(conn, item_id)


def merge(conn: sqlite3.Connection, facet: str, from_name: str, to_name: str) -> None:
    from_name = from_name.strip()
    to_name = to_name.strip()
    if not from_name or not to_name or from_name.lower() == to_name.lower():
        return

    if facet == "topic":
        from_id = _find_tag(conn, from_name)
        to_id = ensure_tag(conn, to_name)
        if not from_id or from_id == to_id:
            return
        conn.execute(
            "INSERT OR IGNORE INTO item_tag (item_id, tag_id, source, created_at) "
            "SELECT item_id, ?, source, created_at FROM item_tag WHERE tag_id = ?",
            (to_id, from_id),
        )
        conn.execute("DELETE FROM item_tag WHERE tag_id = ?", (from_id,))
        conn.execute(
            "INSERT OR REPLACE INTO tag_alias (alias, tag_id, created_at) VALUES (?, ?, ?)",
            (from_name, to_id, now()),
        )
        conn.execute("UPDATE tag SET deleted_at = ? WHERE id = ?", (now(), from_id))

    elif facet == "author":
        row = conn.execute(
            "SELECT id FROM author WHERE display_name = ? COLLATE NOCASE", (from_name,)
        ).fetchone()
        if not row:
            return
        from_id = row["id"]
        to_id = ensure_author(conn, to_name)
        if from_id == to_id:
            return
        conn.execute(
            "INSERT OR IGNORE INTO item_author (item_id, author_id, position) "
            "SELECT item_id, ?, position FROM item_author WHERE author_id = ?",
            (to_id, from_id),
        )
        conn.execute("DELETE FROM item_author WHERE author_id = ?", (from_id,))
        conn.execute(
            "DELETE FROM author WHERE id = ? AND NOT EXISTS "
            "(SELECT 1 FROM item_author WHERE author_id = ?)",
            (from_id, from_id),
        )

    elif facet == "org":
        row = conn.execute(
            "SELECT id FROM org WHERE name = ? COLLATE NOCASE", (from_name,)
        ).fetchone()
        if not row:
            return
        from_id = row["id"]
        to_id = ensure_org(conn, to_name)
        if from_id == to_id:
            return
        conn.execute(
            "INSERT OR IGNORE INTO item_org (item_id, org_id, position) "
            "SELECT item_id, ?, position FROM item_org WHERE org_id = ?",
            (to_id, from_id),
        )
        conn.execute("DELETE FROM item_org WHERE org_id = ?", (from_id,))
        conn.execute(
            "DELETE FROM org WHERE id = ? AND NOT EXISTS "
            "(SELECT 1 FROM item_org WHERE org_id = ?)",
            (from_id, from_id),
        )
    else:
        raise ValueError("unknown facet")

    conn.commit()


# ---------------------------------------------------------------- 文件

def _store_pdf(
    conn: sqlite3.Connection, item_id: str, filename: str, data: bytes
) -> str:
    """把 PDF 写进 files/ 并插入 file 行；失败时回滚，不留下半条数据。"""
    digest = hashlib.sha256(data).hexdigest()
    stem = Path(filename or "paper").stem or "paper"
    slug = re.sub(r'[\\/:*?"<>|\s]+', "_", stem).strip("_")[:48] or "paper"
    prefix = (item_id.split("_")[-1] or item_id)[:8]
    rel_path = f"files/{prefix}_{slug}.pdf"
    dest = LIBRARY / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    ts = now()
    try:
        dest.write_bytes(data)
        conn.execute(
            "INSERT INTO file (id, item_id, rel_path, sha256, size, mime, "
            "original_name, added_at) VALUES (?, ?, ?, ?, ?, 'application/pdf', ?, ?)",
            (new_id("fil"), item_id, rel_path, digest, len(data), filename or "", ts),
        )
        conn.execute("UPDATE item SET updated_at = ? WHERE id = ?", (ts, item_id))
        conn.commit()
    except Exception:
        conn.rollback()
        if dest.exists():
            dest.unlink()
        raise
    return rel_path


def get_item_file(conn: sqlite3.Connection, item_id: str) -> dict | None:
    row = conn.execute(
        "SELECT rel_path, original_name, mime FROM file WHERE item_id = ? "
        "ORDER BY added_at LIMIT 1",
        (item_id,),
    ).fetchone()
    if not row:
        return None
    path = (LIBRARY / row["rel_path"]).resolve()
    try:
        path.relative_to(LIBRARY.resolve())
    except ValueError:
        return None
    if not path.exists():
        return None
    return {
        "path": path,
        "name": row["original_name"] or path.name,
        "mime": row["mime"] or "application/pdf",
    }


def attach_pdf(conn: sqlite3.Connection, item_id: str, filename: str, data: bytes) -> dict:
    """给已有条目补一个 PDF。"""
    if not data.startswith(b"%PDF"):
        return {"status": "error", "reason": "not a PDF", "filename": filename}
    if not conn.execute(
        "SELECT 1 FROM item WHERE id = ? AND deleted_at IS NULL", (item_id,)
    ).fetchone():
        return {"status": "missing", "reason": "item not found"}
    if conn.execute("SELECT 1 FROM file WHERE item_id = ?", (item_id,)).fetchone():
        return {"status": "exists", "item_id": item_id}

    digest = hashlib.sha256(data).hexdigest()
    other = conn.execute(
        "SELECT item_id FROM file WHERE sha256 = ?", (digest,)
    ).fetchone()
    if other:
        return {"status": "duplicate", "item_id": other["item_id"], "filename": filename}

    _store_pdf(conn, item_id, filename, data)
    conn.execute("DELETE FROM passage WHERE item_id = ?", (item_id,))
    conn.commit()
    return {"status": "attached", "item_id": item_id, "filename": filename}


def import_pdf(conn: sqlite3.Connection, filename: str, data: bytes) -> dict:
    if not data.startswith(b"%PDF"):
        return {"status": "error", "reason": "not a PDF", "filename": filename}

    digest = hashlib.sha256(data).hexdigest()
    existing = conn.execute(
        "SELECT item_id FROM file WHERE sha256 = ?", (digest,)
    ).fetchone()
    if existing:
        return {
            "status": "duplicate",
            "item_id": existing["item_id"],
            "filename": filename,
        }

    item_id = new_id("itm")
    stem = Path(filename or "paper").stem or "paper"
    ts = now()
    try:
        conn.execute(
            "INSERT INTO item (id, type, title, abstract, status, language, "
            "needs_review, added_at, updated_at) "
            "VALUES (?, 'article', ?, '', 'todo', 'en', 1, ?, ?)",
            (item_id, stem, ts, ts),
        )
        _store_pdf(conn, item_id, filename, data)
    except Exception:
        conn.rollback()
        raise

    return {"status": "imported", "item_id": item_id, "title": stem, "filename": filename}
