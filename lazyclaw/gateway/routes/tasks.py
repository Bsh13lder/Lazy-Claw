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
    # Per-task budget allocation — a slice of the parent project's budget.
    # `None` leaves it alone; `0` clears it.
    allocated_budget: float | None = None


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


@router.post("/{task_id}/ai-describe")
async def ai_describe_task_route(
    task_id: str,
    user: User = Depends(get_current_user),
):
    """One-click AI explanation: generates a short description of what the
    task is about (from title + project) and saves it. If a description
    already exists, the AI text is appended under a divider instead of
    overwriting the user's notes."""
    from lazyclaw.tasks.ai_describe import describe_task

    task = await get_task(_config, user.id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    text = await describe_task(
        _config, user.id,
        title=task.get("title") or "",
        category=task.get("category"),
        existing_description=task.get("description"),
    )
    if not text:
        raise HTTPException(
            status_code=503,
            detail="AI worker is unavailable right now — try again in a moment.",
        )

    existing = (task.get("description") or "").strip()
    if existing:
        merged = f"{existing}\n\n---\n_AI:_ {text}"
    else:
        merged = text

    await update_task(_config, user.id, task_id, description=merged)
    refreshed = await get_task(_config, user.id, task_id)
    return {"task": refreshed, "ai_text": text}


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


# ---------------------------------------------------------------------------
# Reschedule — NL phrase or worker-LLM "smart" suggestion
# ---------------------------------------------------------------------------


class RescheduleBody(BaseModel):
    # `phrase` is the user's typed input (e.g. "tomorrow 3pm", "+2h",
    # "next Monday", "snooze 30m"). Required for mode=nl.
    phrase: str | None = Field(default=None, max_length=500)
    # mode=smart — backend picks a sensible new time via the worker LLM
    # given the task's title + current schedule. No user input needed.
    mode: Literal["nl", "smart"] = "nl"


@router.post("/{task_id}/reschedule")
async def reschedule_task_route(
    task_id: str,
    body: RescheduleBody,
    user: User = Depends(get_current_user),
):
    """Reschedule a task from NL or via worker-LLM suggestion.

    ``mode=nl``: parse ``phrase`` with ``nl_time`` (regex) → patch the task.
    ``mode=smart``: ask the ECO worker for a sensible reschedule phrase
    based on title + current due/reminder, then run that through the same
    NL parser. Returns ``{task, applied}`` where ``applied`` is the human-
    readable summary the UI shows in the toast.
    """
    task = await get_task(_config, user.id, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    from lazyclaw.tasks.nl_time import parse as nl_parse
    from lazyclaw.tasks.timezone import get_user_tz

    user_tz = await get_user_tz(_config, user.id)

    if body.mode == "smart":
        suggested = await _smart_reschedule_phrase(_config, user.id, task)
        if not suggested:
            raise HTTPException(
                status_code=503,
                detail="Smart reschedule unavailable — worker LLM offline.",
            )
        phrase_to_use = suggested
    else:
        phrase_to_use = (body.phrase or "").strip()
        if not phrase_to_use:
            raise HTTPException(status_code=400, detail="Phrase is required for mode=nl")

    parsed = nl_parse(phrase_to_use, tz=user_tz)
    if not parsed.reminder_at and not parsed.due_date:
        raise HTTPException(
            status_code=422,
            detail=f"Couldn't parse a time from {phrase_to_use!r}. "
            "Try 'tomorrow 3pm', 'next Monday', '+2h', 'snooze 30m'.",
        )

    updates: dict = {}
    if parsed.reminder_at:
        updates["reminder_at"] = parsed.reminder_at
    if parsed.due_date:
        updates["due_date"] = parsed.due_date

    try:
        ok = await update_task(_config, user.id, task_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")

    refreshed = await get_task(_config, user.id, task_id)
    return {
        "task": refreshed,
        "applied": phrase_to_use,
        "mode": body.mode,
    }


async def _smart_reschedule_phrase(config, user_id: str, task: dict) -> str | None:
    """Ask the worker LLM for a one-line reschedule phrase for ``task``.

    Returns the raw phrase (e.g. "tomorrow 9am", "+2 days") or None when
    the LLM is unavailable. The caller feeds the result into the same
    nl_time parser used for user-typed phrases — no second pipeline.
    """
    from lazyclaw.llm.eco_router import EcoRouter, ROLE_WORKER
    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    title = (task.get("title") or "").strip()
    priority = task.get("priority") or "medium"
    cur_due = task.get("due_date") or "—"
    cur_rem = task.get("reminder_at") or "—"

    prompt = (
        "You reschedule a task to a sensible new time based on its title, "
        "priority, and current schedule. Pick a reasonable working-hours "
        "slot. Reply with ONE short phrase only that the task parser can "
        "handle. Examples of valid replies:\n"
        "  tomorrow 9am\n  next Monday 14:00\n  +2 days\n  Friday 10am\n"
        "  in 3 hours\n\n"
        "No quotes. No explanation. No prefix. Just the phrase.\n\n"
        f"Task: {title}\n"
        f"Priority: {priority}\n"
        f"Current due: {cur_due}\n"
        f"Current reminder: {cur_rem}"
    )
    try:
        eco = EcoRouter(config, LLMRouter(config))
        response = await eco.chat(
            [LLMMessage(role="user", content=prompt)],
            user_id=user_id,
            role=ROLE_WORKER,
        )
    except Exception:
        return None
    raw = (response.content or "").strip() if response else ""
    # Strip quotes / a leading bullet — the parser is permissive but the
    # model sometimes wraps anyway.
    raw = raw.strip('"').strip("'").lstrip("-•·").strip()
    raw = raw.splitlines()[0].strip()
    return raw or None
