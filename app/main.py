from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai as ai_src
from . import metadata as metadata_src
from . import repo
from .db import init_db, connect

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DOCS = ROOT / "docs"

app = FastAPI(title="Colophon", version="0.1.0")
init_db()

app.mount("/design", StaticFiles(directory=DOCS), name="design")
app.mount("/static", StaticFiles(directory=WEB), name="static")


# ---------------------------------------------------------------- 页面

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


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


class FetchQueueIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


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
        rows = conn.execute(
            "SELECT id, title, doi, arxiv_id FROM item "
            "WHERE needs_review = 1 AND deleted_at IS NULL ORDER BY rowid LIMIT ?",
            (payload.limit,),
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
