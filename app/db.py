from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = Path(os.environ.get("COLOPHON_LIBRARY", str(ROOT / "library"))).expanduser().resolve()
DB_PATH = LIBRARY / "library.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


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


def init_db() -> None:
    ensure_library()
    conn = connect()
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
