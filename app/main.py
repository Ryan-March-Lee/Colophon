from __future__ import annotations

import json
import os
import stat
import string
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai as ai_src
from . import metadata as metadata_src
from . import repo
from .db import init_db, connect
from .paths import resource_root

RESOURCE = resource_root()
WEB = RESOURCE / "web"
DOCS = RESOURCE / "docs"

app = FastAPI(title="Colophon", version="0.1.0")
init_db()
_review_conn = connect()
try:
    repo.mark_items_missing_metadata_for_review(_review_conn)
finally:
    _review_conn.close()

if DOCS.is_dir():
    app.mount("/design", StaticFiles(directory=DOCS), name="design")
app.mount("/static", StaticFiles(directory=WEB), name="static")


# ---------------------------------------------------------------- 页面

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    # 开发时改完页面，重开窗口就能拿到最新 HTML；避免 WebView2 用启发式缓存显示旧界面
    return FileResponse(
        WEB / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    icon = WEB / "favicon.ico"
    if not icon.is_file():
        raise HTTPException(status_code=404, detail="no favicon")
    return FileResponse(icon, media_type="image/x-icon")


# ---------------------------------------------------------------- 模型

class ItemPatch(BaseModel):
    status: Literal["todo", "reading", "read"] | None = None
    starred: bool | None = None
    memory: str | None = None
    note_md: str | None = None


class TagIn(BaseModel):
    facet: Literal["topic", "author", "org", "year"]
    name: str = Field(min_length=1, max_length=120)


class MergeIn(BaseModel):
    facet: Literal["topic", "author", "org"]
    from_name: str = Field(min_length=1, max_length=120)
    to_name: str = Field(min_length=1, max_length=120)


class FieldsIn(BaseModel):
    title: str | None = None
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    url: str | None = None
    authors: list[str] | None = None
    orgs: list[str] | None = None


class FetchQueueIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    itemId: str | None = None


class ImportPathsIn(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=5000)
    folder: str | None = None


class AISettingsIn(BaseModel):
    baseUrl: str | None = None
    model: str | None = None
    apiKey: str | None = None
    clearKey: bool = False


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    itemIds: list[str] = Field(default_factory=list, max_length=500)
    conversationId: str | None = None


class SuggestIn(BaseModel):
    limit: int = Field(default=4, ge=1, le=6)


# ---------------------------------------------------------------- 读

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "app": "Colophon", "version": "0.1.0"}


@app.get("/api/items")
def list_items() -> dict:
    conn = connect()
    try:
        items = repo.list_items(conn)
    finally:
        conn.close()
    return {"items": items, "total": len(items)}


@app.get("/api/items/{item_id}")
def get_item(item_id: str) -> dict:
    conn = connect()
    try:
        item = repo.get_item(conn, item_id)
    finally:
        conn.close()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    return item


@app.get("/api/items/{item_id}/file")
def item_file(item_id: str) -> FileResponse:
    conn = connect()
    try:
        found = repo.get_item_file(conn, item_id)
    finally:
        conn.close()
    if not found:
        raise HTTPException(status_code=404, detail="no file for this item")
    return FileResponse(
        found["path"],
        media_type=found["mime"],
        filename=found["name"],
        content_disposition_type="inline",
    )


@app.post("/api/items/{item_id}/file")
async def attach_item_file(item_id: str, file: UploadFile = File(...)) -> dict:
    data = await file.read()
    conn = connect()
    try:
        result = repo.attach_pdf(conn, item_id, file.filename or "paper.pdf", data)
    finally:
        conn.close()
    if result["status"] == "missing":
        raise HTTPException(status_code=404, detail="item not found")
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["reason"])
    return result


@app.get("/api/import/queue")
def import_queue() -> dict:
    conn = connect()
    try:
        items = repo.import_queue(conn)
    finally:
        conn.close()
    return {"items": items, "total": len(items)}


@app.get("/api/tags")
def list_tags() -> dict:
    conn = connect()
    try:
        tags = repo.list_tags(conn)
    finally:
        conn.close()
    return {"tags": tags, "total": len(tags)}


# ---------------------------------------------------------------- 写

@app.patch("/api/items/{item_id}")
def patch_item(item_id: str, payload: ItemPatch) -> dict:
    fields = payload.model_dump(exclude_unset=True)
    conn = connect()
    try:
        item = repo.patch_item(conn, item_id, fields)
    finally:
        conn.close()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    return item


@app.post("/api/items/{item_id}/tags")
def add_tag(item_id: str, payload: TagIn) -> dict:
    conn = connect()
    try:
        try:
            item = repo.add_tag(conn, item_id, payload.facet, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    return item


@app.delete("/api/items/{item_id}/tags")
def remove_tag(item_id: str, facet: str, name: str) -> dict:
    conn = connect()
    try:
        try:
            item = repo.remove_tag(conn, item_id, facet, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    return item


@app.post("/api/merge")
def merge(payload: MergeIn) -> dict:
    conn = connect()
    try:
        try:
            repo.merge(conn, payload.facet, payload.from_name, payload.to_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    return {"ok": True}


# ---------------------------------------------------------------- 元数据

@app.post("/api/items/{item_id}/metadata")
def fetch_item_metadata(item_id: str) -> dict:
    conn = connect()
    try:
        state = repo.metadata_state(conn, item_id)
        if state is None:
            raise HTTPException(status_code=404, detail="item not found")
        found = repo.get_item_file(conn, item_id)
        report = metadata_src.resolve(
            filename=found["name"] if found else "",
            path=found["path"] if found else None,
            title_hint=state["title"],
            known_doi=state["doi"],
            known_arxiv=state["arxiv_id"],
            current=state,
            conn=conn,
            use_ai=True,
        )
        if report.get("status") != "ok":
            return {"ok": False, "report": report}
        was_complete = bool(
            state["year"]
            and state["authors"]
            and state["orgs"]
            and (state["venue"] or state["doi"] or state["arxiv_id"])
        )
        keep_review = bool(report.get("needsCheck")) or (
            was_complete and state["needs_review"]
        )
        item = repo.apply_metadata(
            conn,
            item_id,
            report["meta"],
            keep_review=keep_review,
        )
        if report["meta"].get("conflict"):
            report["message"] = report.get("message", "") + "（DOI / arXiv 已在另一条上，未重复写入）"
    finally:
        conn.close()
    return {"ok": True, "item": item, "report": report}


@app.post("/api/import/queue/fetch")
def fetch_queue_metadata(payload: FetchQueueIn) -> dict:
    conn = connect()
    results: list[dict] = []
    try:
        item_filter = " AND id = ?" if payload.itemId is not None else ""
        params = (payload.itemId, payload.limit) if payload.itemId is not None else (payload.limit,)
        rows = conn.execute(
            "SELECT id, title, doi, arxiv_id FROM item "
            "WHERE needs_review = 1 AND deleted_at IS NULL" + item_filter + " ORDER BY rowid LIMIT ?",
            params,
        ).fetchall()
        for row in rows:
            found = repo.get_item_file(conn, row["id"])
            state = repo.metadata_state(conn, row["id"])
            try:
                report = metadata_src.resolve(
                    filename=found["name"] if found else "",
                    path=found["path"] if found else None,
                    title_hint=row["title"],
                    known_doi=row["doi"] or "",
                    known_arxiv=row["arxiv_id"] or "",
                    current=state,
                    conn=conn,
                    use_ai=False,  # 批量只跑规则，避免一次请求里连续等模型
                )
            except Exception as exc:  # 单条失败不阻塞整批
                report = {"status": "error", "message": str(exc)}
            if report.get("status") == "ok":
                keep_review = bool(
                    state
                    and state["year"]
                    and state["authors"]
                    and state["orgs"]
                    and (state["venue"] or state["doi"] or state["arxiv_id"])
                    and state["needs_review"]
                )
                repo.apply_metadata(
                    conn, row["id"], report["meta"], keep_review=keep_review
                )
                if report["meta"].get("conflict"):
                    report["message"] = report.get("message", "") + "（已在另一条上，未重复写入）"
            results.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": report.get("status"),
                    "message": report.get("message", ""),
                }
            )
    finally:
        conn.close()

    return {
        "total": len(results),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "results": results,
    }


@app.patch("/api/items/{item_id}/fields")
def patch_fields(item_id: str, payload: FieldsIn) -> dict:
    fields = payload.model_dump(exclude_unset=True)
    conn = connect()
    try:
        try:
            item = repo.patch_fields(conn, item_id, fields)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        conn.close()
    if not item:
        raise HTTPException(status_code=404, detail="item not found")
    return item


# ---------------------------------------------------------------- AI

@app.get("/api/settings/ai")
def get_ai_settings() -> dict:
    conn = connect()
    try:
        return ai_src.public_settings(conn)
    finally:
        conn.close()


@app.put("/api/settings/ai")
def put_ai_settings(payload: AISettingsIn) -> dict:
    conn = connect()
    try:
        ai_src.save_settings(
            conn,
            base_url=payload.baseUrl,
            model=payload.model,
            api_key=payload.apiKey,
            clear_key=payload.clearKey,
        )
        return ai_src.public_settings(conn)
    finally:
        conn.close()


@app.post("/api/ai/ask")
def ai_ask(payload: AskIn) -> dict:
    conn = connect()
    try:
        return ai_src.ask(
            conn,
            payload.question,
            payload.itemIds,
            payload.conversationId,
        )
    except ai_src.MissingKey as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ai_src.NoContent as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ai_src.ModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/items/{item_id}/suggest-tags")
def suggest_tags(item_id: str, payload: SuggestIn) -> dict:
    conn = connect()
    try:
        return ai_src.suggest_tags(conn, item_id, payload.limit)
    except ai_src.MissingKey as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ai_src.NoContent as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ai_src.ModelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    conn = connect()
    try:
        conv = conn.execute(
            "SELECT id, title, scope_json, created_at FROM conversation WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        rows = conn.execute(
            "SELECT role, content, cites_json, model, created_at FROM message "
            "WHERE conversation_id = ? ORDER BY rowid",
            (conversation_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "id": conv["id"],
        "title": conv["title"],
        "scope": json.loads(conv["scope_json"]),
        "createdAt": conv["created_at"],
        "messages": [
            {
                "role": row["role"],
                "content": row["content"],
                "citations": json.loads(row["cites_json"] or "[]"),
                "model": row["model"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------- 导入

MAX_FS_FILES = 2000
_FS_SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", "node_modules"}


def _require_local_browser(request: Request) -> None:
    # 自定义请求头挡住跨站简单请求；校验 Host 防止 DNS 重绑定。
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if (
        request.client is None
        or request.client.host not in local_hosts
        or request.url.hostname not in local_hosts
        or request.headers.get("x-colophon-import") != "1"
    ):
        raise HTTPException(status_code=403, detail="只能在本机 Colophon 中浏览文件")


def _local_path(raw: str) -> Path:
    try:
        path = Path(raw)
        if not path.is_absolute() or (os.name == "nt" and path.drive.startswith("\\")):
            raise ValueError
        resolved = path.resolve(strict=True)
        if os.name == "nt" and resolved.drive.startswith("\\"):
            raise ValueError
        return resolved
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="本地路径不存在或不可读") from exc


def _fs_hidden(entry: os.DirEntry) -> bool:
    """隐藏项不列：点开头的、以及 Windows 标记为隐藏/系统的。"""
    if entry.name.startswith(".") or entry.name in _FS_SKIP_DIRS:
        return True
    if os.name != "nt":
        return False
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return True
    flags = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0) | getattr(
        stat, "FILE_ATTRIBUTE_SYSTEM", 0
    )
    return bool(attrs & flags)


def _windows_known_folder(folder_id: str) -> Path | None:
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    guid = (ctypes.c_ubyte * 16).from_buffer_copy(UUID(folder_id).bytes_le)
    path = ctypes.c_void_p()
    get_path = ctypes.windll.shell32.SHGetKnownFolderPath
    get_path.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)
    )
    get_path.restype = ctypes.c_long
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = None
    try:
        if get_path(guid, 0, None, ctypes.byref(path)) == 0 and path.value:
            return Path(ctypes.wstring_at(path.value))
        return None
    finally:
        if path.value:
            free(path)


def _fs_roots() -> list[dict]:
    roots: list[dict] = []
    seen: set[Path] = set()

    def add(name: str, path: Path, kind: str) -> None:
        try:
            resolved = _local_path(str(path))
            if resolved in seen or not resolved.is_dir():
                return
        except HTTPException:
            return
        seen.add(resolved)
        roots.append({"name": name, "path": str(resolved), "kind": kind})

    home = Path.home()
    for name, fallback, kind, folder_id in (
        ("桌面", "Desktop", "desktop", "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"),
        ("下载", "Downloads", "downloads", "374DE290-123F-4565-9164-39C4925E467B"),
        ("文档", "Documents", "documents", "FDD39AD0-238F-46AF-ADB4-6C85480369C7"),
    ):
        add(name, _windows_known_folder(folder_id) or home / fallback, kind)
    add("主目录", home, "home")
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            add(f"{letter}:\\", Path(f"{letter}:\\"), "drive")
    else:
        add("/", Path("/"), "drive")
    return roots


@app.get("/api/fs/list", dependencies=[Depends(_require_local_browser)])
def fs_list(path: str | None = None) -> dict:
    """应用内导入窗的目录列表：只列文件夹和 PDF，不列隐藏项。"""
    if not path:
        return {
            "path": "",
            "parent": None,
            "roots": _fs_roots(),
            "dirs": [],
            "files": [],
            "truncated": False,
        }

    target = _local_path(path)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="文件夹不存在或不可读")

    dirs: list[dict] = []
    files: list[dict] = []
    truncated = False
    try:
        with os.scandir(target) as entries:
            for entry in entries:
                try:
                    if _fs_hidden(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({"name": entry.name, "path": entry.path})
                    elif entry.is_file(follow_symlinks=False) and (
                        entry.name.lower().endswith(".pdf")
                    ):
                        files.append(
                            {
                                "name": entry.name,
                                "path": entry.path,
                            }
                        )
                except OSError:
                    continue
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"无法读取这个文件夹：{exc}") from exc

    dirs.sort(key=lambda d: d["name"].casefold())
    files.sort(key=lambda f: f["name"].casefold())
    if len(files) > MAX_FS_FILES:
        files = files[:MAX_FS_FILES]
        truncated = True

    parent = target.parent
    return {
        "path": str(target),
        "parent": None if parent == target else str(parent),
        "roots": [],
        "dirs": dirs,
        "files": files,
        "truncated": truncated,
    }


def _walk_pdfs(root: Path, errors: list[dict]):
    def record_error(exc: OSError) -> None:
        errors.append({"status": "error", "reason": str(exc), "filename": exc.filename or ""})

    for dirpath, dirnames, filenames in os.walk(root, onerror=record_error):
        # Windows 上 junction 不一定被 os.walk 当成符号链接。
        for name in dirnames[:]:
            try:
                info = (Path(dirpath) / name).lstat()
                if stat.S_ISLNK(info.st_mode) or (
                    getattr(info, "st_reparse_tag", None)
                    == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
                ):
                    dirnames.remove(name)
            except OSError as exc:
                dirnames.remove(name)
                record_error(exc)
        dirnames.sort(key=str.casefold)
        for name in sorted(filenames, key=str.casefold):
            if name.lower().endswith(".pdf"):
                yield Path(dirpath) / name


@app.post("/api/import/paths", dependencies=[Depends(_require_local_browser)])
def import_paths(payload: ImportPathsIn) -> dict:
    """按路径导入选中的 PDF，或整个文件夹（含各级子文件夹）。"""
    if bool(payload.folder) == bool(payload.paths):
        raise HTTPException(status_code=400, detail="请选择 PDF 或一个文件夹")

    results: list[dict] = []
    if payload.folder:
        root = _local_path(payload.folder)
        if not root.is_dir():
            raise HTTPException(status_code=404, detail="文件夹不存在或不可读")
        targets = _walk_pdfs(root, results)
    else:
        targets = map(Path, payload.paths)

    conn = connect()
    try:
        for target in targets:
            try:
                if not target.is_absolute() or target.suffix.lower() != ".pdf" or target.is_symlink():
                    raise ValueError("不是可导入的本地 PDF 文件")
                source = _local_path(str(target))
                if not source.is_file():
                    raise ValueError("不是可导入的本地 PDF 文件")
                results.append(
                    repo.import_pdf(conn, target.name, source.read_bytes())
                )
            except (OSError, ValueError, HTTPException) as exc:
                reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
                results.append({"status": "error", "reason": reason, "filename": target.name})
    finally:
        conn.close()

    return {
        "total": len(results),
        "imported": sum(1 for r in results if r["status"] == "imported"),
        "duplicates": sum(1 for r in results if r["status"] == "duplicate"),
        "errors": [r for r in results if r["status"] == "error"],
        "results": results,
    }


@app.post("/api/import")
async def import_files(files: list[UploadFile] = File(...)) -> dict:
    results = []
    conn = connect()
    try:
        for upload in files:
            data = await upload.read()
            results.append(
                repo.import_pdf(conn, upload.filename or "paper.pdf", data)
            )
    finally:
        conn.close()

    return {
        "imported": sum(1 for r in results if r["status"] == "imported"),
        "duplicates": sum(1 for r in results if r["status"] == "duplicate"),
        "errors": [r for r in results if r["status"] == "error"],
        "results": results,
    }
