from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from .paths import LIBRARY, schema_path

DB_PATH = LIBRARY / "library.db"
SCHEMA_PATH = schema_path()


def ensure_library() -> None:
    for sub in ("", "files", ".colophon", ".trash", "backups"):
        (LIBRARY / sub).mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    ensure_library()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _drop_legacy_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(item)")}
    # 必须先移除引用阅读状态列的索引，才能删除该列。
    conn.execute("DROP INDEX IF EXISTS ix_item_status")
    if "status" in existing:
        conn.execute("ALTER TABLE item DROP COLUMN status")

    for name in ("memory", "ai_brief"):
        if name in existing:
            try:
                conn.execute(f"ALTER TABLE item DROP COLUMN {name}")
            except sqlite3.OperationalError:
                # 旧版 SQLite 不支持 DROP COLUMN 时，已停用的摘要列可以保留。
                pass


def init_db() -> None:
    ensure_library()
    conn = connect()
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("BEGIN IMMEDIATE")
        _drop_legacy_columns(conn)
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
