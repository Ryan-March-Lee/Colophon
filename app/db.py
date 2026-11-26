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


LEGACY_COLUMNS = {
    # 一篇文献不压成一句话：这两列已整体移除，老库需要显式删掉。
    # schema.sql 只有 IF NOT EXISTS，删列不能靠它。
    "item": ("memory", "ai_brief"),
}


def _drop_legacy_columns(conn: sqlite3.Connection) -> None:
    for table, columns in LEGACY_COLUMNS.items():
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name in columns:
            if name in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
                except sqlite3.OperationalError:
                    # 老 SQLite（< 3.35）不支持 DROP COLUMN：代码已不再读这两列，
                    # 留着也不影响，下次换到新版本时会自动删掉。
                    pass


def init_db() -> None:
    ensure_library()
    conn = connect()
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _drop_legacy_columns(conn)
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
