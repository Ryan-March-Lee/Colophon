from __future__ import annotations

import hashlib
import re
import sqlite3
import stat
from pathlib import Path

from .db import LIBRARY, new_id, now
from .identity import arxiv_key, bibliographic_matches, doi_key, pdf_keys
from .metadata import extract_abstract, missing_fields, pdf_text

TAG_COLORS = ["#1c7566", "#a2672c", "#7757a6", "#5b6f83", "#8a5a22", "#2f7d4f"]


# ---------------------------------------------------------------- 读取

def _row_to_item(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return _rows_to_items(conn, [row])[0]


def list_items(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM item WHERE deleted_at IS NULL ORDER BY rowid"
    ).fetchall()
    return _rows_to_items(conn, rows)


def _rows_to_items(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    """一次查齐作者 / 单位 / 标签 / 分组，再拼装。

    逐条调用 `_row_to_item` 会按条目数 × 3 次查询打一遍库（65 篇 200 次往返），
    几百篇时首页每次筛选都要等它。这里固定 4 次查询，与条目数无关。
    """
    ids = [row["id"] for row in rows]
    if not ids:
        return []

    authors: dict[str, list[str]] = {}
    orgs: dict[str, list[str]] = {}
    topics: list[dict] = []
    for chunk_start in range(0, len(ids), 900):
        chunk = ids[chunk_start : chunk_start + 900]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT ia.item_id, a.display_name FROM item_author ia "
            f"JOIN author a ON a.id = ia.author_id "
            f"WHERE ia.item_id IN ({marks}) ORDER BY ia.position, a.display_name",
            chunk,
        ):
            authors.setdefault(r["item_id"], []).append(r["display_name"])
        for r in conn.execute(
            f"SELECT io.item_id, o.name FROM item_org io "
            f"JOIN org o ON o.id = io.org_id "
            f"WHERE io.item_id IN ({marks}) ORDER BY io.position, o.name",
            chunk,
        ):
            orgs.setdefault(r["item_id"], []).append(r["name"])
        for r in conn.execute(
            f"SELECT it.item_id, t.id AS tag_id, t.name, t.color, g.id AS group_id "
            f"FROM item_tag it JOIN tag t ON t.id = it.tag_id "
            f"LEFT JOIN tag_group_member m ON m.tag_id = t.id "
            f"LEFT JOIN tag_group g ON g.id = m.group_id AND g.deleted_at IS NULL "
            f"WHERE it.item_id IN ({marks}) AND t.deleted_at IS NULL "
            f"ORDER BY it.created_at, t.name",
            chunk,
        ):
            topics.append(dict(r))

    files = {}
    for chunk_start in range(0, len(ids), 900):
        chunk = ids[chunk_start : chunk_start + 900]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT item_id, rel_path, original_name FROM file WHERE item_id IN ({marks}) ORDER BY added_at",
            chunk,
        ):
            path = LIBRARY / r["rel_path"]
            if path.is_file():
                files.setdefault(r["item_id"], r["original_name"] or path.name)

    topic_map: dict[str, list[str]] = {}
    topic_ids: dict[str, list[str]] = {}
    topic_group: dict[str, str | None] = {}
    for t in topics:
        names = topic_map.setdefault(t["item_id"], [])
        if t["name"] not in names:
            names.append(t["name"])
        ids = topic_ids.setdefault(t["item_id"], [])
        if t["tag_id"] not in ids:
            ids.append(t["tag_id"])
        topic_group.setdefault(t["name"], t["group_id"])
    return [
        _dict_to_item(
            row,
            authors.get(row["id"], []),
            orgs.get(row["id"], []),
            topic_map.get(row["id"], []),
            topic_ids.get(row["id"], []),
            topic_group,
            files.get(row["id"]),
        )
        for row in rows
    ]


def _dict_to_item(
    row: sqlite3.Row,
    authors: list[str],
    orgs: list[str],
    topics: list[str],
    topic_tag_ids: list[str],
    topic_group: dict[str, str | None],
    file_name: str | None,
) -> dict:
    item_id = row["id"]
    missing = missing_fields({**dict(row), "authors": authors, "orgs": orgs})
    return {
        "id": item_id,
        "title": row["title"],
        "authors": authors,
        "venue": row["venue"] or "",
        "doi": row["doi"] or "",
        "arxivId": row["arxiv_id"] or "",
        "url": row["url"] or "",
        "abstract": row["abstract"],
        "note": row["note_md"],
        "year": row["year"],
        "starred": bool(row["starred"]),
        "addedAt": row["added_at"],
        "needsReview": bool(row["needs_review"]),
        "hasFile": bool(file_name),
        "fileName": file_name or "",
        "missing": missing,
        "tags": {
            "year": [str(row["year"])] if row["year"] else [],
            "author": authors,
            "org": orgs,
            "topic": topics,
        },
        # 检索内核要判断「未打标」和标签组的布尔项，名字会变（合并、改名），
        # 所以给一份稳定的 id。名字仍留着给列表行和悬停定义用。
        "topicIds": topic_tag_ids,
        "topicGroups": [topic_group.get(name) for name in topics],
    }


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
        "SELECT title, abstract, year, venue, doi, arxiv_id, needs_review FROM item "
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
    orgs = [
        r["name"]
        for r in conn.execute(
            "SELECT o.name FROM item_org io "
            "JOIN org o ON o.id = io.org_id "
            "WHERE io.item_id = ? ORDER BY io.position",
            (item_id,),
        )
    ]
    return {
        "title": row["title"] or "",
        "abstract": row["abstract"],
        "year": row["year"],
        "venue": row["venue"] or "",
        "doi": row["doi"] or "",
        "arxiv_id": row["arxiv_id"] or "",
        "authors": authors,
        "orgs": orgs,
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


def list_tag_groups(conn: sqlite3.Connection) -> dict:
    """按组返回标签：groups[] + unfiled[]。

    没有 tag_group_member 行的标签就是「未分组」；未分组不是数据库行，
    永远排在 groups 之外、由前端固定在组区末尾。
    """
    groups = [
        dict(row)
        for row in conn.execute(
            "SELECT id, name, color, position FROM tag_group "
            "WHERE deleted_at IS NULL ORDER BY position, rowid"
        ).fetchall()
    ]
    tags = [
        dict(row)
        for row in conn.execute(
            "SELECT t.id, t.name, t.color, t.definition, "
            "       g.id AS group_id, g.name AS group_name, "
            "       COUNT(DISTINCT i.id) AS count "
            "FROM tag t "
            "LEFT JOIN tag_group_member m ON m.tag_id = t.id "
            "LEFT JOIN tag_group g ON g.id = m.group_id AND g.deleted_at IS NULL "
            "LEFT JOIN item_tag it ON it.tag_id = t.id "
            "LEFT JOIN item i ON i.id = it.item_id AND i.deleted_at IS NULL "
            "WHERE t.deleted_at IS NULL "
            "GROUP BY t.id, g.id, g.name ORDER BY t.name"
        ).fetchall()
    ]

    by_group: dict[str, list[dict]] = {g["id"]: [] for g in groups}
    unfiled: list[dict] = []
    for tag in tags:
        gid = tag.pop("group_id")
        tag.pop("group_name", None)
        if gid and gid in by_group:
            by_group[gid].append(tag)
        else:
            # 组被软删 / 组行缺失时，标签退回未分组，不消失
            unfiled.append(tag)

    for g in groups:
        g["tags"] = by_group[g["id"]]
        g["count"] = len(g["tags"])
    for tag in unfiled:
        tag["group_id"] = None

    unfiled.sort(key=lambda t: (-t["count"], t["name"]))
    return {
        "groups": groups,
        "unfiled": unfiled,
        "total": len(tags),
        "unfiledCount": len(unfiled),
    }


def ensure_tag_group(conn: sqlite3.Connection, name: str, color: str = "") -> str:
    """按名字复用组（大小写不敏感，忽略软删）；没有就建一个，排到末尾。"""
    name = name.strip()
    if not name:
        raise ValueError("empty group name")
    row = conn.execute(
        "SELECT id, deleted_at FROM tag_group WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        if row["deleted_at"]:
            conn.execute(
                "UPDATE tag_group SET deleted_at = NULL WHERE id = ?", (row["id"],)
            )
        return row["id"]
    group_id = new_id("grp")
    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM tag_group WHERE deleted_at IS NULL"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO tag_group (id, name, color, position, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (group_id, name, color, position, now()),
    )
    return group_id


def set_tag_group(
    conn: sqlite3.Connection, tag_id: str, group_id: str | None
) -> dict | None:
    """把一个标签放进某个组；group_id 为 None 表示移回未分组。"""
    tag = conn.execute(
        "SELECT id FROM tag WHERE id = ? AND deleted_at IS NULL", (tag_id,)
    ).fetchone()
    if not tag:
        return None
    if group_id:
        group = conn.execute(
            "SELECT id FROM tag_group WHERE id = ? AND deleted_at IS NULL", (group_id,)
        ).fetchone()
        if not group:
            raise ValueError("unknown group")
        position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM tag_group_member "
            "WHERE group_id = ?",
            (group_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO tag_group_member (tag_id, group_id, position, added_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tag_id) DO UPDATE SET group_id = excluded.group_id, "
            "position = excluded.position, added_at = excluded.added_at",
            (tag_id, group_id, position, now()),
        )
    else:
        conn.execute("DELETE FROM tag_group_member WHERE tag_id = ?", (tag_id,))
    conn.commit()
    return {"tag_id": tag_id, "group_id": group_id or None}


def set_tag_definition(
    conn: sqlite3.Connection, tag_id: str, definition: str
) -> dict | None:
    """标签的一句话定义：压的是一个概念，不是一篇文献。"""
    tag = conn.execute(
        "SELECT id FROM tag WHERE id = ? AND deleted_at IS NULL", (tag_id,)
    ).fetchone()
    if not tag:
        return None
    definition = " ".join((definition or "").split())[:120]
    conn.execute(
        "UPDATE tag SET definition = ? WHERE id = ?", (definition, tag_id)
    )
    conn.commit()
    return {"tag_id": tag_id, "definition": definition}


def delete_tag(
    conn: sqlite3.Connection, tag_id: str, expected_count: int
) -> tuple[int, bool] | None:
    """返回当前的文献数和是否已删除，标签不存在时返回 None。"""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT COUNT(i.id) AS count FROM tag t "
            "LEFT JOIN item_tag it ON it.tag_id = t.id "
            "LEFT JOIN item i ON i.id = it.item_id AND i.deleted_at IS NULL "
            "WHERE t.id = ? AND t.deleted_at IS NULL GROUP BY t.id",
            (tag_id,),
        ).fetchone()
        if row is None:
            return None
        affected = row["count"]
        if affected != expected_count:
            return affected, False
        # 外键级联移除文献关联、别名和分组成员行。
        conn.execute("DELETE FROM tag WHERE id = ?", (tag_id,))
        return affected, True


def delete_tag_group(conn: sqlite3.Connection, group_id: str) -> int | None:
    """软删组并清掉成员行：标签退回未分组，不级联删标签。返回受影响的标签数。

    组不存在（或已经软删）时返回 None，供接口区分 404 与「空组删除」。
    """
    group = conn.execute(
        "SELECT id FROM tag_group WHERE id = ? AND deleted_at IS NULL", (group_id,)
    ).fetchone()
    if not group:
        return None
    affected = conn.execute(
        "SELECT COUNT(*) FROM tag_group_member WHERE group_id = ?", (group_id,)
    ).fetchone()[0]
    conn.execute("DELETE FROM tag_group_member WHERE group_id = ?", (group_id,))
    conn.execute("UPDATE tag_group SET deleted_at = ? WHERE id = ?", (now(), group_id))
    conn.commit()
    return affected


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


def ensure_tag(
    conn: sqlite3.Connection, name: str, group_id: str | None = None
) -> str:
    """按名字取标签（补全别名、软删复用）；没有就新建。

    group_id 给定时：新标签直接落进该组；已有但还没有组归属的标签一并落进去。
    已经在别的组里的标签**不会**被悄悄改组——一个标签只属于一个组。
    """
    name = name.strip()
    row = conn.execute(
        "SELECT tag_id FROM tag_alias WHERE alias = ? COLLATE NOCASE", (name,)
    ).fetchone()
    tag_id = None
    if row:
        tag_id = row["tag_id"]
    else:
        row = conn.execute(
            "SELECT id, deleted_at FROM tag WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            if row["deleted_at"]:
                conn.execute(
                    "UPDATE tag SET deleted_at = NULL WHERE id = ?", (row["id"],)
                )
            tag_id = row["id"]
        else:
            tag_id = new_id("tg")
            color = TAG_COLORS[sum(ord(c) for c in name) % len(TAG_COLORS)]
            conn.execute(
                "INSERT INTO tag (id, name, color, created_at) VALUES (?, ?, ?, ?)",
                (tag_id, name, color, now()),
            )

    if group_id:
        group = conn.execute(
            "SELECT id FROM tag_group WHERE id = ? AND deleted_at IS NULL", (group_id,)
        ).fetchone()
        if not group:
            raise ValueError("unknown group")
        if not conn.execute(
            "SELECT 1 FROM tag_group_member WHERE tag_id = ?", (tag_id,)
        ).fetchone():
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM tag_group_member "
                "WHERE group_id = ?",
                (group_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO tag_group_member (tag_id, group_id, position, added_at) "
                "VALUES (?, ?, ?, ?)",
                (tag_id, group_id, position, now()),
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
    allowed = {"starred", "note_md"}
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
    state = metadata_state(conn, item_id)
    if state is None:
        return []
    missing = missing_fields(state)
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


def _replace_orgs(conn: sqlite3.Connection, item_id: str, orgs: list[str]) -> None:
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
    """把抓到的元数据写回；摘要只填空，其余字段只在有新值时覆盖。

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
        "UPDATE item SET title = ?, abstract = CASE "
        "WHEN abstract = ? THEN ? ELSE abstract END, year = ?, venue = ?, doi = ?, "
        "arxiv_id = ?, url = ?, updated_at = ? WHERE id = ?",
        (
            pick("title", row["title"]),
            row["abstract"],
            row["abstract"] if row["abstract"].strip() else pick("abstract", ""),
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
    if isinstance(fields.get("orgs"), list):
        _replace_orgs(conn, item_id, fields["orgs"])

    if any(key in fields for key in ("title", "abstract")):
        conn.execute(
            "DELETE FROM passage WHERE item_id = ? AND kind = 'abstract'", (item_id,)
        )

    _recompute_review(conn, item_id)
    conn.commit()
    return get_item(conn, item_id)


def mark_items_missing_metadata_for_review(conn: sqlite3.Connection) -> int:
    """把仍缺单位或摘要的旧条目放回补全队列。"""
    cursor = conn.execute(
        "UPDATE item SET needs_review = 1 "
        "WHERE deleted_at IS NULL AND needs_review = 0 AND ("
        "  trim(abstract, char(9, 10, 11, 12, 13, 32, 160, 12288)) = '' OR NOT EXISTS ("
        "    SELECT 1 FROM item_org io WHERE io.item_id = item.id"
        "  )"
        ")"
    )
    conn.commit()
    return cursor.rowcount


def add_tag(
    conn: sqlite3.Connection,
    item_id: str,
    facet: str,
    name: str,
    group_id: str | None = None,
    group_name: str | None = None,
) -> dict | None:
    """打标。topic 时可以顺带给标签定组（group_id 或 group_name 二选一）。

    手动打标应带组；AI 建议 / 导入继承不带，落到未分组。
    """
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
        target_group = group_id
        if not target_group and group_name and group_name.strip():
            target_group = ensure_tag_group(conn, group_name)
        tag_id = ensure_tag(conn, name, group_id=target_group)
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

    _recompute_review(conn, item_id)
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

    _recompute_review(conn, item_id)
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
        # 组归属随保留的标签走：保留的标签还没组时才继承被并掉那个的组，
        # 已经有组就保持原样（一个标签只属于一个组）。
        from_member = conn.execute(
            "SELECT group_id, position FROM tag_group_member WHERE tag_id = ?", (from_id,)
        ).fetchone()
        if from_member and not conn.execute(
            "SELECT 1 FROM tag_group_member WHERE tag_id = ?", (to_id,)
        ).fetchone():
            conn.execute(
                "INSERT INTO tag_group_member (tag_id, group_id, position, added_at) "
                "VALUES (?, ?, ?, ?)",
                (to_id, from_member["group_id"], from_member["position"], now()),
            )
        conn.execute("DELETE FROM tag_group_member WHERE tag_id = ?", (from_id,))
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

def delete_item(conn: sqlite3.Connection, item_id: str) -> dict | None:
    """删除条目、关联数据及书库中的 PDF 副本。"""
    files_deleted = 0
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute(
            "SELECT 1 FROM item WHERE id = ? AND deleted_at IS NULL", (item_id,)
        ).fetchone():
            return None

        files_dir = LIBRARY / "files"
        paths = []
        rows = conn.execute("SELECT rel_path FROM file WHERE item_id = ?", (item_id,))
        for row in rows:
            path = LIBRARY / row["rel_path"]
            if files_dir.resolve() != files_dir or path.parent != files_dir or path.suffix.lower() != ".pdf":
                raise OSError("附件不在书库的 PDF 目录中")
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(mode):
                raise OSError("附件不是普通 PDF 文件")
            if conn.execute(
                "SELECT 1 FROM file WHERE rel_path = ? COLLATE NOCASE AND item_id <> ?",
                (row["rel_path"], item_id),
            ).fetchone():
                raise OSError("该 PDF 还被其他文献使用")
            paths.append(path)

        # 先执行数据库删除，让约束先于磁盘删除生效；删文件失败时回滚书库条目。
        conn.execute("DELETE FROM item WHERE id = ?", (item_id,))
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            files_deleted += 1
    return {"ok": True, "filesDeleted": files_deleted}


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
        _fill_pdf_abstract(conn, item_id, dest)
        conn.execute("UPDATE item SET updated_at = ? WHERE id = ?", (ts, item_id))
        conn.commit()
    except Exception:
        conn.rollback()
        if dest.exists():
            dest.unlink()
        raise
    return rel_path


def get_item_file(conn: sqlite3.Connection, item_id: str) -> dict | None:
    rows = conn.execute(
        "SELECT rel_path, original_name, mime FROM file WHERE item_id = ? "
        "ORDER BY added_at",
        (item_id,),
    )
    for row in rows:
        path = (LIBRARY / row["rel_path"]).resolve()
        if path.is_relative_to(LIBRARY.resolve()) and path.is_file():
            return {"path": path, "name": row["original_name"] or path.name,
                    "mime": row["mime"] or "application/pdf"}
    return None


def _fill_pdf_abstract(conn: sqlite3.Connection, item_id: str, path: Path) -> None:
    row = conn.execute("SELECT abstract FROM item WHERE id = ?", (item_id,)).fetchone()
    if row is None or row["abstract"].strip():
        return
    abstract = extract_abstract(pdf_text(path))
    if abstract:
        cursor = conn.execute(
            "UPDATE item SET abstract = ?, updated_at = ? WHERE id = ? "
            "AND deleted_at IS NULL AND abstract = ?",
            (abstract, now(), item_id, row["abstract"]),
        )
        if cursor.rowcount:
            conn.execute("DELETE FROM passage WHERE item_id = ? AND kind = 'abstract'", (item_id,))
            _recompute_review(conn, item_id)


def attach_pdf(conn: sqlite3.Connection, item_id: str, filename: str, data: bytes) -> dict:
    """给已有条目补一个 PDF。"""
    if not data.startswith(b"%PDF"):
        return {"status": "error", "reason": "not a PDF", "filename": filename}

    digest = hashlib.sha256(data).hexdigest()
    with conn:
        # 在写入附件前锁定书库，避免检查条目后，它已被另一请求删除。
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute(
            "SELECT 1 FROM item WHERE id = ? AND deleted_at IS NULL", (item_id,)
        ).fetchone():
            return {"status": "missing", "reason": "item not found"}
        if get_item_file(conn, item_id):
            return {"status": "exists", "item_id": item_id}

        other = conn.execute(
            "SELECT item_id FROM file WHERE sha256 = ?", (digest,)
        ).fetchone()
        if other and other["item_id"] != item_id:
            return {"status": "duplicate", "item_id": other["item_id"], "filename": filename}

        conn.execute("DELETE FROM file WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM passage WHERE item_id = ?", (item_id,))
        _store_pdf(conn, item_id, filename, data)
    return {"status": "attached", "item_id": item_id, "filename": filename}


def import_pdf(conn: sqlite3.Connection, filename: str, data: bytes) -> dict:
    if not data.startswith(b"%PDF"):
        return {"status": "error", "reason": "not a PDF", "filename": filename}

    digest = hashlib.sha256(data).hexdigest()
    stem = Path(filename or "paper").stem or "paper"
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT item_id, rel_path FROM file WHERE sha256 = ?", (digest,)
        ).fetchone()
        if existing and (LIBRARY / existing["rel_path"]).is_file():
            return {"status": "duplicate", "item_id": existing["item_id"], "filename": filename}

        fingerprint_matches = {row["item_id"] for row in conn.execute(
            "SELECT h.item_id FROM shared_pdf_hash h JOIN item i ON i.id = h.item_id "
            "WHERE h.sha256 = ? AND i.deleted_at IS NULL", (digest,)
        )}
        if existing:
            fingerprint_matches.add(existing["item_id"])

        dois, arxiv = pdf_keys(data)
        claimed = fingerprint_matches | bibliographic_matches(conn, dois, arxiv)
        if len(claimed) == 1:
            row = conn.execute("SELECT doi, arxiv_id FROM item WHERE id = ?", (next(iter(claimed)),)).fetchone()
            local_doi, local_arxiv = doi_key(row["doi"]), arxiv_key(row["arxiv_id"])
            incompatible = ((len(dois) == 1 and local_doi and local_doi not in dois)
                            or (len(arxiv) == 1 and local_arxiv and local_arxiv not in arxiv))
            ambiguous = (len(dois) > 1 or len(arxiv) > 1) and not fingerprint_matches
            if ambiguous or incompatible:
                return {"status": "error", "reason": "PDF 标识不唯一或冲突，请在文献详情中手动补 PDF", "filename": filename}
        if len(claimed) > 1:
            return {"status": "error", "reason": "PDF 匹配到多篇文献，请在文献详情中手动补 PDF", "filename": filename}
        if claimed:
            item_id = claimed.pop()
            if get_item_file(conn, item_id):
                return {"status": "duplicate", "item_id": item_id, "filename": filename}
            conn.execute("DELETE FROM file WHERE item_id = ?", (item_id,))
            conn.execute("DELETE FROM passage WHERE item_id = ?", (item_id,))
            _store_pdf(conn, item_id, filename, data)
            return {"status": "attached", "item_id": item_id, "filename": filename}

        item_id = new_id("itm")
        ts = now()
        conn.execute(
            "INSERT INTO item (id, type, title, abstract, language, doi, arxiv_id, "
            "needs_review, added_at, updated_at) "
            "VALUES (?, 'article', ?, '', 'en', ?, ?, 1, ?, ?)",
            (item_id, stem, next(iter(dois)) if len(dois) == 1 else None,
             next(iter(arxiv)) if len(arxiv) == 1 else None, ts, ts),
        )
        _store_pdf(conn, item_id, filename, data)

    return {"status": "imported", "item_id": item_id, "title": stem, "filename": filename}
