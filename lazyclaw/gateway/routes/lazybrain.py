"""LazyBrain REST API — CRUD, backlinks, graph, journal, tags."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.lazybrain import (
    ask,
    autolink,
    canvas,
    embeddings,
    events,
    graph,
    journal,
    metadata_suggest,
    recap,
    store,
    topic_rollup,
)

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


# ─── Phase 2 AI request models ─────────────────────────────────────────

class AutolinkRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    use_llm: bool = True


class SuggestMetadataRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=10, ge=1, le=30)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=20)


class TopicRollupRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)


class MorningBriefingRequest(BaseModel):
    force: bool = False


# ─── Phase 3 canvas models ──────────────────────────────────────────────

class CanvasSaveRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    payload: dict


# ---------------------------------------------------------------------------
# Notes CRUD
# ---------------------------------------------------------------------------

@router.get("/notes")
async def list_notes_route(
    tag: str | None = None,
    pinned: bool = False,
    limit: int = 500,
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


@router.post("/notes/{note_id}/mark-task-done")
async def mark_task_done_route(
    note_id: str, user: User = Depends(get_current_user)
):
    """Mark the task mirrored by this LazyBrain note as done.

    Resolves the note id back to the underlying task via the
    ``lazybrain_note_id`` column, then calls ``complete_task`` so the
    tasks table, reminder jobs, and the LazyBrain mirror all update
    consistently. Returns ``{ status: "completed", task_id }`` on success
    or 404 when no matching task exists for that note.
    """
    from lazyclaw.tasks import store as task_store

    task_id = await task_store.find_task_id_by_note(_config, user.id, note_id)
    if not task_id:
        raise HTTPException(
            status_code=404,
            detail="No task is linked to this note. The note may not be a task mirror.",
        )
    ok = await task_store.complete_task(_config, user.id, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found or already deleted")
    return {"status": "completed", "task_id": task_id, "note_id": note_id}


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
# Graph node positions — survive reloads + cross-device sync.
# ---------------------------------------------------------------------------

class GraphPositionsUpsert(BaseModel):
    mode: str = Field(pattern=r"^(category|neural-link)$")
    # {note_id: [x, y]} — tuple decodes as a 2-element list over JSON.
    positions: dict[str, list[float]] = Field(default_factory=dict)


@router.get("/graph/positions")
async def get_graph_positions_route(
    mode: str,
    user: User = Depends(get_current_user),
):
    try:
        pos = await graph.get_positions(_config, user.id, mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"mode": mode, "positions": {k: [x, y] for k, (x, y) in pos.items()}}


@router.post("/graph/positions")
async def save_graph_positions_route(
    body: GraphPositionsUpsert,
    user: User = Depends(get_current_user),
):
    # Cap payload size — 2000 nodes is already way beyond the 500-node graph
    # cap, so anything larger is either a bug or abuse.
    if len(body.positions) > 2000:
        raise HTTPException(status_code=413, detail="too many positions")
    shaped: dict[str, tuple[float, float]] = {}
    for note_id, coords in body.positions.items():
        if not isinstance(coords, list) or len(coords) != 2:
            continue
        shaped[note_id] = (float(coords[0]), float(coords[1]))
    try:
        written = await graph.save_positions(_config, user.id, body.mode, shaped)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"saved": written}


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


# ---------------------------------------------------------------------------
# Phase 2 — AI-native endpoints
# ---------------------------------------------------------------------------

@router.post("/autolink")
async def autolink_route(
    body: AutolinkRequest,
    user: User = Depends(get_current_user),
):
    return await autolink.suggest_links(
        _config, user.id, body.text, use_llm=body.use_llm,
    )


@router.post("/suggest-metadata")
async def suggest_metadata_route(
    body: SuggestMetadataRequest,
    user: User = Depends(get_current_user),
):
    existing = await store.list_tags(_config, user.id)
    tags_seed = [row["tag"] for row in existing[:40]]
    return await metadata_suggest.suggest_metadata(
        _config, user.id, body.content, existing_tags=tags_seed,
    )


@router.post("/semantic-search")
async def semantic_search_route(
    body: SemanticSearchRequest,
    user: User = Depends(get_current_user),
):
    return await embeddings.semantic_search(
        _config, user.id, body.query, k=body.k,
    )


@router.post("/ask")
async def ask_route(
    body: AskRequest,
    user: User = Depends(get_current_user),
):
    return await ask.ask_notes(_config, user.id, body.question, k=body.k)


@router.post("/topic-rollup")
async def topic_rollup_route(
    body: TopicRollupRequest,
    user: User = Depends(get_current_user),
):
    return await topic_rollup.topic_rollup(_config, user.id, body.topic)


@router.post("/morning-briefing")
async def morning_briefing_route(
    body: MorningBriefingRequest,
    user: User = Depends(get_current_user),
):
    return await recap.build_morning_briefing(_config, user.id, force=body.force)


@router.post("/reindex-embeddings")
async def reindex_embeddings_route(
    user: User = Depends(get_current_user),
):
    return await embeddings.reindex_user(_config, user.id)


# ---------------------------------------------------------------------------
# Phase 3 — Canvas
# ---------------------------------------------------------------------------

@router.get("/canvas")
async def list_canvases_route(user: User = Depends(get_current_user)):
    boards = await canvas.list_boards(_config, user.id)
    return {"boards": boards}


@router.get("/canvas/{board_id}")
async def get_canvas_route(
    board_id: str, user: User = Depends(get_current_user)
):
    board = await canvas.get_board(_config, user.id, board_id)
    if not board:
        raise HTTPException(status_code=404, detail="Canvas not found")
    return board


@router.post("/canvas")
async def save_canvas_route(
    body: CanvasSaveRequest,
    user: User = Depends(get_current_user),
):
    return await canvas.save_board(
        _config, user.id, body.name, body.payload, board_id=body.id,
    )


@router.delete("/canvas/{board_id}")
async def delete_canvas_route(
    board_id: str, user: User = Depends(get_current_user)
):
    ok = await canvas.delete_board(_config, user.id, board_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Canvas not found")
    return {"status": "deleted", "id": board_id}
