"""Tasks API — encrypted user todo list.

Exposes the Task Manager (``lazyclaw.tasks.store``) to the web UI. The store
is already scoped by ``user_id`` and all free-text fields are AES-256-GCM
encrypted at rest; we just hand decrypted dicts back to the owner.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.tasks.store import (
    complete_task,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    update_task,
)

_config = load_config()

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


OwnerFilter = Literal["user", "agent", "all"]
StatusFilter = Literal["todo", "in_progress", "done", "all"]
BucketFilter = Literal["today", "upcoming", "someday", "all"]


class CreateTaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    category: str | None = Field(default=None, max_length=100)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    due_date: str | None = None
    reminder_at: str | None = None
    recurring: str | None = None
    tags: list[str] | None = None


class UpdateTaskBody(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    status: Literal["todo", "in_progress", "done"] | None = None
    due_date: str | None = None
    reminder_at: str | None = None
    tags: list[str] | None = None


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
