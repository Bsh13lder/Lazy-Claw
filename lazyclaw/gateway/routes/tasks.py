"""Tasks API — encrypted user todo list.

Exposes the Task Manager (``lazyclaw.tasks.store``) to the web UI. The store
is already scoped by ``user_id`` and all free-text fields are AES-256-GCM
encrypted at rest; we just hand decrypted dicts back to the owner.

Includes a ``/parse`` helper that turns a free-text phrase like "tomorrow
at 9 buy milk urgent" into a structured draft using the fast regex parser,
with an LLM fallback for anything regex can't handle.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.tasks.ai_parse import ai_parse_task
from lazyclaw.tasks.nl_time import parse_full as regex_parse_full
from lazyclaw.tasks.store import (
    complete_task,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    set_steps,
    toggle_step,
    update_task,
)

_config = load_config()

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


OwnerFilter = Literal["user", "agent", "all"]
StatusFilter = Literal["todo", "in_progress", "done", "all"]
BucketFilter = Literal["today", "upcoming", "someday", "all"]


class StepDraft(BaseModel):
    id: str | None = None
    title: str
    done: bool = False


class CreateTaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    category: str | None = Field(default=None, max_length=100)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    due_date: str | None = None
    reminder_at: str | None = None
    recurring: str | None = None
    tags: list[str] | None = None
    steps: list[StepDraft] | None = None


class UpdateTaskBody(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    status: Literal["todo", "in_progress", "done"] | None = None
    due_date: str | None = None
    reminder_at: str | None = None
    tags: list[str] | None = None


class ParseBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    mode: Literal["fast", "ai"] = "fast"


class SetStepsBody(BaseModel):
    steps: list[StepDraft]


@router.get("")
async def list_tasks_route(
    user: User = Depends(get_current_user),
    owner: OwnerFilter = Query("user"),
    status: StatusFilter = Query("all"),
    bucket: BucketFilter = Query("all"),
):
    """List tasks for the current user.

    Default filters lean towards "what the user cares about right now" —
    owner=user (things the user dictated, excluding agent-created background
    work). Pass ``owner=all`` to include agent-owned entries.
    """
    tasks = await list_tasks(
        _config,
        user.id,
        owner=None if owner == "all" else owner,
        status=None if status == "all" else status,
        bucket=None if bucket == "all" else bucket,
    )
    return {"tasks": tasks, "count": len(tasks)}


@router.post("")
async def create_task_route(
    body: CreateTaskBody,
    user: User = Depends(get_current_user),
):
    """Create a user-owned task. Agent-owned tasks are created by skills."""
    steps_payload = (
        [s.model_dump() for s in body.steps] if body.steps else None
    )
    task = await create_task(
        _config,
        user.id,
        title=body.title,
        description=body.description,
        category=body.category,
        priority=body.priority,
        owner="user",
        due_date=body.due_date,
        reminder_at=body.reminder_at,
        recurring=body.recurring,
        tags=body.tags,
        steps=steps_payload,
    )
    return {"task": task}


@router.patch("/{task_id}")
async def update_task_route(
    task_id: str,
    body: UpdateTaskBody = Body(default_factory=UpdateTaskBody),
    user: User = Depends(get_current_user),
):
    """Patch an existing task. Only non-null fields are applied."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = await update_task(_config, user.id, task_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found")
    task = await get_task(_config, user.id, task_id)
    return {"task": task}


@router.post("/{task_id}/complete")
async def complete_task_route(
    task_id: str,
    user: User = Depends(get_current_user),
):
    """Tick a task off. Handles recurring: next occurrence auto-created."""
    ok = await complete_task(_config, user.id, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "done", "id": task_id}


@router.delete("/{task_id}")
async def delete_task_route(
    task_id: str,
    user: User = Depends(get_current_user),
):
    """Remove a task entirely (plus its reminder job)."""
    ok = await delete_task(_config, user.id, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "deleted", "id": task_id}


# ---------------------------------------------------------------------------
# Sub-task steps
# ---------------------------------------------------------------------------


@router.put("/{task_id}/steps")
async def set_steps_route(
    task_id: str,
    body: SetStepsBody,
    user: User = Depends(get_current_user),
):
    """Replace the full sub-task checklist for a task."""
    normalized = await set_steps(
        _config, user.id, task_id, [s.model_dump() for s in body.steps],
    )
    if normalized is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"steps": normalized}


@router.post("/{task_id}/steps/{step_id}/toggle")
async def toggle_step_route(
    task_id: str,
    step_id: str,
    user: User = Depends(get_current_user),
):
    """Flip the done flag on a single step. Returns the refreshed task."""
    task = await toggle_step(_config, user.id, task_id, step_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task or step not found")
    return {"task": task}


# ---------------------------------------------------------------------------
# Quick-add parser — regex fast path, LLM for everything else.
# ---------------------------------------------------------------------------


@router.post("/parse")
async def parse_task_route(
    body: ParseBody,
    user: User = Depends(get_current_user),
):
    """Parse a free-text phrase into a task draft.

    ``mode=fast`` uses the local regex parser — millisecond latency, works
    for the top ~10 phrasings including Spanish. ``mode=ai`` routes through
    the ECO worker for complex input.
    """
    if body.mode == "ai":
        draft = await ai_parse_task(_config, user.id, body.text)
    else:
        draft = regex_parse_full(body.text)
    return {"draft": draft, "mode": body.mode}
