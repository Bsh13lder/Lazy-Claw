"""LazyBrain REST API — CRUD, backlinks, graph, journal, tags."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.lazybrain import events, graph, journal, store

_config = load_config()

router = APIRouter(prefix="/api/lazybrain", tags=["lazybrain"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class NoteCreate(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    title: str | None = Field(default=None, max_length=300)
    tags: list[str] | None = None
    importance: int = Field(default=5, ge=1, le=10)
    pinned: bool = False
    trace_session_id: str | None = None


class NoteUpdate(BaseModel):
    content: str | None = Field(default=None, max_length=200_000)
    title: str | None = Field(default=None, max_length=300)
    tags: list[str] | None = None
    importance: int | None = Field(default=None, ge=1, le=10)
    pinned: bool | None = None


class JournalAppend(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)


# ---------------------------------------------------------------------------
# Notes CRUD
# ---------------------------------------------------------------------------

@router.get("/notes")
async def list_notes_route(
    tag: str | None = None,
    pinned: bool = False,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
):
    notes = await store.list_notes(
        _config,
        user.id,
        tag=tag,
        pinned_only=pinned,
        limit=limit,
        offset=offset,
    )
    return {"notes": notes}


@router.post("/notes")
async def create_note_route(
    body: NoteCreate, user: User = Depends(get_current_user)
):
    # Anything coming through the REST route is a user action — stamp it.
    tags = list(body.tags or [])
    if not any(t.startswith("owner/") for t in tags):
        tags.append("owner/user")
    note = await store.save_note(
        _config,
        user.id,
        content=body.content,
        title=body.title,
        tags=tags,
        importance=body.importance,
        pinned=body.pinned,
        trace_session_id=body.trace_session_id,
    )
    events.publish_note_saved(user.id, note["id"], note["title"], note["tags"])
    return note


@router.get("/notes/{note_id}")
async def get_note_route(note_id: str, user: User = Depends(get_current_user)):
    note = await store.get_note(_config, user.id, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.patch("/notes/{note_id}")
async def update_note_route(
    note_id: str,
    body: NoteUpdate,
    user: User = Depends(get_current_user),
):
    note = await store.update_note(
        _config,
        user.id,
        note_id,
        content=body.content,
        title=body.title,
        tags=body.tags,
        importance=body.importance,
        pinned=body.pinned,
    )
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    events.publish_note_saved(user.id, note["id"], note["title"], note["tags"])
    return note


@router.delete("/notes/{note_id}")
async def delete_note_route(
    note_id: str, user: User = Depends(get_current_user)
):
    note = await store.get_note(_config, user.id, note_id)
    deleted = await store.delete_note(_config, user.id, note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    events.publish_note_deleted(
        user.id, note_id, note["title"] if note else None
    )
    return {"status": "deleted", "id": note_id}


@router.get("/notes/{note_id}/backlinks")
async def backlinks_route(
    note_id: str, user: User = Depends(get_current_user)
):
    note = await store.get_note(_config, user.id, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    linked = await store.get_backlinks(_config, user.id, note["title_key"] or note_id)
    return {"note_id": note_id, "backlinks": linked}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search")
async def search_route(
    q: str,
    tag: str | None = None,
    limit: int = 20,
    user: User = Depends(get_current_user),
):
    results = await store.search_notes(
        _config, user.id, q, tag=tag, limit=limit
    )
    return {"query": q, "results": results}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@router.get("/graph")
async def graph_route(
    root_id: str | None = None,
    depth: int = 1,
    limit: int = 500,
    user: User = Depends(get_current_user),
):
    if root_id:
        return await graph.get_neighbors(
            _config, user.id, root_id, depth=depth
        )
    return await graph.get_graph(_config, user.id, limit=limit)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

@router.get("/journal/{iso_date}")
async def get_journal_route(
    iso_date: str, user: User = Depends(get_current_user)
):
    try:
        note = await journal.get_journal(_config, user.id, iso_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not note:
        return {"date": iso_date, "note": None}
    return {"date": iso_date, "note": note}


@router.post("/journal/{iso_date}")
async def append_journal_route(
    iso_date: str,
    body: JournalAppend,
    user: User = Depends(get_current_user),
):
    try:
        note = await journal.append_journal(
            _config, user.id, body.content, iso_date
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    events.publish_note_saved(user.id, note["id"], note["title"], note["tags"])
    return note


@router.get("/journal")
async def list_journal_route(
    limit: int = 14, user: User = Depends(get_current_user)
):
    notes = await journal.list_journal(_config, user.id, limit=limit)
    return {"notes": notes}


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@router.get("/tags")
async def tags_route(user: User = Depends(get_current_user)):
    return {"tags": await store.list_tags(_config, user.id)}
