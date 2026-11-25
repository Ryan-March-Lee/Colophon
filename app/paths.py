"""打包与开发两套路径。

开发时资源在仓库根，书库默认在 `library/`；
PyInstaller 打包后只读资源在 `sys._MEIPASS`（onedir 为 `_internal/`），
书库默认落在 exe 旁边的 `library/`，整个文件夹可以直接搬走。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根：web/、docs/、app/schema.sql 都在它下面。"""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def schema_path() -> Path:
    return resource_root() / "app" / "schema.sql"


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def default_library() -> Path:
    if not FROZEN:
        return resource_root() / "library"
    beside_exe = Path(sys.executable).resolve().parent / "library"
    if _writable(beside_exe):
        return beside_exe
    # 放在只读目录（例如 Program Files）时退回用户目录
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    fallback = Path(base) / "Colophon" if base else Path.home() / ".colophon"
    return fallback


LIBRARY = Path(os.environ.get("COLOPHON_LIBRARY") or default_library()).expanduser().resolve()
