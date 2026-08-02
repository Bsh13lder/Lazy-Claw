"""Tasks API — encrypted user todo list.

Exposes the Task Manager (``lazyclaw.tasks.store``) to the web UI. The store
is already scoped by ``user_id`` and all free-text fields are AES-256-GCM
encrypted at rest; we just hand decrypted dicts back to the owner.

Includes a ``/parse`` helper that turns a free-text phrase like "tomorrow
at 9 buy milk urgent" into a structured draft using the fast regex parser,
with an LLM fallback for anything regex can't handle.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.tasks.ai_parse import ai_parse_task
from lazyclaw.tasks.nl_time import parse_full as regex_parse_full
from lazyclaw.tasks.pre_reminders import resolve_pre_reminders
from lazyclaw.tasks.store import (
    CommentLimitReached,
    add_comment,
    complete_task,
    create_task,
    delete_comment,
    delete_task,
    get_task,
    get_task_changes,
    list_tasks,
    normalize_reminder_to_utc,
    set_steps,
    toggle_step,
    update_task,
)

logger = logging.getLogger(__name__)

_config = load_config()

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


OwnerFilter = Literal["user", "agent", "all"]
StatusFilter = Literal["todo", "in_progress", "done", "all"]
BucketFilter = Literal["today", "upcoming", "someday", "all"]


class StepDraft(BaseModel):
    id: str | None = None
    title: str
    done: bool = False


class CommentBody(BaseModel):
    # Optional client-minted id for offline-first idempotent replay
    # (same convention as CreateTaskBody.id).
    id: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=2000)
    subtask_id: str | None = Field(default=None, max_length=64)


class CreateTaskBody(BaseModel):
    # Optional client-minted id for offline-first idempotent replay.
    # When provided, the server uses it as the task id. A second POST
    # with the same id returns the existing task without duplicating it.
    id: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    category: str | None = Field(default=None, max_length=100)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    due_date: str | None = None
    reminder_at: str | None = None
    recurring: str | None = None
    # Recurrence end — "YYYY-MM-DD" (series runs through the end of that day
    # in the user's tz) or a full ISO datetime. Omit/None = repeats forever.
    recur_until: str | None = None
    tags: list[str] | None = None
    steps: list[StepDraft] | None = None
    # Advance reminders. Omit (None) to auto-derive from the user's
    # reminder_offsets when a reminder_at is set. Send [] to opt this task
    # out, or a list of offsets/ISO datetimes to override.
    pre_reminders: list[str] | None = None


class UpdateTaskBody(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    status: Literal["todo", "in_progress", "done"] | None = None
    due_date: str | None = None
    reminder_at: str | None = None
    # Standard 5-field cron expression for a recurring task (the mobile/web
    # repeat picker authors it; the backend respawns the next occurrence on
    # complete via get_next_run). An empty string clears the recurrence — it is
    # falsy, so the respawn path treats it as "does not repeat".
    recurring: str | None = None
    # Recurrence end. Empty string or explicit null clears it (mirrors the
    # recurring convention).
    recur_until: str | None = None
    tags: list[str] | None = None
    # Per-task budget allocation — a slice of the parent project's budget.
    # `None` leaves it alone; `0` clears it.
    allocated_budget: float | None = None


class ParseBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    mode: Literal["fast", "ai"] = "fast"


def _validate_recurring(value: str | None) -> None:
    """Reject a ``recurring`` value the cron engine can't schedule.

    Both write endpoints used to persist ANY string here. A human phrase like
    "daily" or "every Monday" saved happily and rendered a "Repeats" chip — then
    at completion time ``complete_task``'s respawn calls ``get_next_run``, which
    raises, gets swallowed by that block's broad ``except`` (it only logs a
    warning), and the series dies with no next occurrence and no user-visible
    error. Fail LOUDLY at the write boundary instead of silently weeks later.

    ``None`` and the empty string are deliberately allowed: an empty string
    CLEARS the recurrence (see the ``UpdateTaskBody.recurring`` comment above) —
    it is falsy, so the respawn path reads it as "does not repeat".
    """
    if value is None or not str(value).strip():
        return

    from lazyclaw.heartbeat.cron import is_valid as cron_is_valid

    if not cron_is_valid(str(value)):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{value!r} is not a valid recurring schedule. Use a 5-field "
                "cron expression — e.g. '0 9 * * 1' (every Monday at 09:00), "
                "'0 8 * * *' (daily at 08:00), '0 9 1 * *' (1st of the month). "
                "Send an empty string to clear the recurrence."
            ),
        )


def _validate_recur_until(value: str | None) -> None:
    """Reject a ``recur_until`` the respawn can't compare. 400, not 500.

    Same fail-loud-at-the-boundary rationale as ``_validate_recurring``: an
    LLM phrase like "in two weeks" stored silently would either be skipped by
    the respawn (series never ends) or mis-flagged as a respawn failure.
    ``None`` / empty string are allowed — they clear the end date.
    """
    if value is None or not str(value).strip():
        return
    from datetime import datetime as _dt

    try:
        _dt.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{value!r} is not a valid recur_until. Use 'YYYY-MM-DD' "
                "(the series runs through the end of that day) or a full ISO "
                "datetime. Send an empty string to clear it."
            ),
        )


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
    logger.debug(
        "[route:tasks] POST create user=%s fields=%s client_id=%s",
        user.id, list(body.model_dump(exclude_unset=True).keys()),
        bool(body.id),
    )
    _validate_recurring(body.recurring)
    _validate_recur_until(body.recur_until)
    steps_payload = (
        [s.model_dump() for s in body.steps] if body.steps else None
    )
    # Normalize BEFORE deriving: mobile posts naive Madrid wall-clock, and
    # deriving offsets from the raw value read it as UTC — the "2h before"
    # heads-up landed exactly AT the reminder. ``create_task`` re-normalizes
    # (idempotent), so both the stored reminder and its advance reminders are
    # computed from the same UTC instant. The PATCH path already does this
    # (``update_task`` re-derives after normalizing).
    reminder_at = normalize_reminder_to_utc(body.reminder_at, user.id)
    # Derive advance reminders from the user's reminder_offsets (or honour an
    # explicit list) so REST/mobile-created timed tasks fire the same
    # Proton-Calendar-style advance reminders as the chat/Telegram path.
    pre_reminders = await resolve_pre_reminders(
        _config,
        user.id,
        reminder_at=reminder_at,
        due_date=body.due_date,
        explicit=body.pre_reminders,
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
        reminder_at=reminder_at,
        recurring=body.recurring,
        recur_until=body.recur_until,
        tags=body.tags,
        steps=steps_payload,
        pre_reminders=pre_reminders,
        task_id=body.id or None,
    )
    return {"task": task}


@router.patch("/{task_id}")
async def update_task_route(
    task_id: str,
    body: UpdateTaskBody = Body(default_factory=UpdateTaskBody),
    user: User = Depends(get_current_user),
):
    """Patch an existing task.

    Uses ``exclude_unset`` so a client can CLEAR a field by sending an explicit
    ``null`` (e.g. ``{"due_date": null}``); a field the request omits is left
    untouched. The old ``if v is not None`` filter conflated "absent" with
    "clear", so clearing due_date / reminder_at / description from the web
    silently 400'd. The store already writes NULL for a None value (and tears
    down the reminder job when reminder_at is cleared). Mobile only ever sends
    the fields it changed (never an explicit null), so it is unaffected.

    ``status='done'`` is NOT written through ``update_task``. The whole
    completion pipeline — recurring respawn, sub-task cascade, reminder-job
    teardown, pulse pause, progress-log ``done`` entry, LazyBrain ✅ mirror —
    lives in ``store.complete_task``; ``update_task`` only flips the column.
    Ticking a task off from the web/mobile detail sheet therefore killed the
    recurring series on its first completion (no next occurrence), left the
    checklist unchecked and kept the reminder job nagging a finished task.
    Any other fields in the same PATCH are applied FIRST, so the respawn is
    built from the post-edit row.

    The reverse transition clears ``completed_at``: re-opening a done task used
    to leave the stamp set, so the row still read as completed to every
    consumer of that column.
    """
    updates = body.model_dump(exclude_unset=True)
    logger.debug(
        "[route:tasks] PATCH id=%s user=%s fields=%s",
        task_id, user.id, list(updates.keys()),
    )
    # Never let a stray null blank the title — the one always-required field.
    if "title" in updates and not (updates["title"] or "").strip():
        updates.pop("title")
    if not updates:
        logger.warning(
            "[route:tasks] PATCH id=%s user=%s -> 400 no fields to update",
            task_id, user.id,
        )
        raise HTTPException(status_code=400, detail="No fields to update")
    if "recurring" in updates:
        _validate_recurring(updates["recurring"])
    if "recur_until" in updates:
        _validate_recur_until(updates["recur_until"])

    # Split the done transition out of the plain-field update.
    new_status = updates.pop("status", None)
    completing = new_status == "done"
    if new_status is not None and not completing:
        updates["status"] = new_status          # re-open: write it normally…
        updates["completed_at"] = None          # …and drop the stale stamp

    if updates:
        updated = await update_task(_config, user.id, task_id, **updates)
    else:
        # ``{"status": "done"}`` on its own leaves nothing for update_task —
        # existence still has to be proven so the 404 contract holds.
        updated = await get_task(_config, user.id, task_id) is not None
    if not updated:
        logger.warning(
            "[route:tasks] PATCH id=%s user=%s -> 404 task not found",
            task_id, user.id,
        )
        raise HTTPException(status_code=404, detail="Task not found")

    if completing:
        # Idempotent in the store (already-done → no-op True), so a replayed
        # mobile sync op can't spawn a duplicate next occurrence.
        ok = await complete_task(_config, user.id, task_id)
        if not ok:
            logger.warning(
                "[route:tasks] PATCH id=%s user=%s -> 404 task not found (complete)",
                task_id, user.id,
            )
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
        logger.warning(
            "[route:tasks] POST complete id=%s user=%s -> 404 task not found",
            task_id, user.id,
        )
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
        logger.warning(
            "[route:tasks] DELETE id=%s user=%s -> 404 task not found",
            task_id, user.id,
        )
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "deleted", "id": task_id}


@router.get("/changes")
async def task_changes_route(
    user: User = Depends(get_current_user),
    since: str | None = Query(
        default=None,
        description=(
            "ISO-8601 datetime. Only tasks updated after this timestamp are "
            "returned. Omit to receive all tasks (full sync). Use the `now` "
            "field from the previous response as the next `since` value."
        ),
    ),
):
    """Delta feed for offline-first clients.

    Returns:
    - ``tasks``: live (non-deleted) tasks updated after ``since``
    - ``deleted``: ids of tasks soft-deleted after ``since``
    - ``now``: server ISO timestamp — pass this as ``since`` next time

    Clients should persist ``now`` locally and send it on the next pull.
    Last-write-wins on ``updated_at`` resolves any conflicts.
    """
    result = await get_task_changes(_config, user.id, since=since)
    logger.debug(
        "[route:tasks] GET changes user=%s since=%s -> tasks=%d deleted=%d now=%s",
        user.id, since,
        len(result.get("tasks") or []),
        len(result.get("deleted") or []),
        result.get("now"),
    )
    return result


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
        logger.warning(
            "[route:tasks] POST ai-describe id=%s user=%s -> 404 task not found",
            task_id, user.id,
        )
        raise HTTPException(status_code=404, detail="Task not found")

    text = await describe_task(
        _config, user.id,
        title=task.get("title") or "",
        category=task.get("category"),
        existing_description=task.get("description"),
    )
    if not text:
        logger.warning(
            "[route:tasks] POST ai-describe id=%s user=%s -> 503 AI worker unavailable",
            task_id, user.id,
        )
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


class PolishExplanationBody(BaseModel):
    explanation_text: str = Field(min_length=1, max_length=5000)


@router.post("/{task_id}/ai-polish-explanation")
async def ai_polish_explanation_route(
    task_id: str,
    body: PolishExplanationBody,
    user: User = Depends(get_current_user),
):
    """Manual explain: the user supplies their OWN rough explanation; the AI
    rewrites it into clean prose and REPLACES the task description (no _AI:_
    append — the user authored this). If the worker is unavailable, the user's
    raw text is saved verbatim rather than failing — never lose their input."""
    from lazyclaw.tasks.ai_polish_explanation import polish_explanation

    task = await get_task(_config, user.id, task_id)
    if task is None:
        logger.warning(
            "[route:tasks] POST ai-polish-explanation id=%s user=%s -> 404 task not found",
            task_id, user.id,
        )
        raise HTTPException(status_code=404, detail="Task not found")

    raw = body.explanation_text.strip()
    polished = await polish_explanation(
        _config, user.id,
        raw_text=raw,
        title=task.get("title") or "",
        category=task.get("category"),
    )
    final = polished or raw  # graceful fallback: keep the user's words

    await update_task(_config, user.id, task_id, description=final)
    refreshed = await get_task(_config, user.id, task_id)
    return {"task": refreshed, "ai_text": final}


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
    logger.debug(
        "[route:tasks] PUT steps id=%s user=%s count=%d",
        task_id, user.id, len(body.steps),
    )
    normalized = await set_steps(
        _config, user.id, task_id, [s.model_dump() for s in body.steps],
    )
    if normalized is None:
        logger.warning(
            "[route:tasks] PUT steps id=%s user=%s -> 404 task not found",
            task_id, user.id,
        )
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
        logger.warning(
            "[route:tasks] POST toggle-step id=%s step=%s user=%s -> 404 task or step not found",
            task_id, step_id, user.id,
        )
        raise HTTPException(status_code=404, detail="Task or step not found")
    return {"task": task}


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@router.post("/{task_id}/comments")
async def add_comment_route(
    task_id: str,
    body: CommentBody,
    user: User = Depends(get_current_user),
):
    """Append one user-authored comment to a task (or one of its subtasks)."""
    try:
        entry = await add_comment(
            _config, user.id, task_id,
            text=body.text, author="user",
            subtask_id=body.subtask_id, comment_id=body.id,
        )
    except CommentLimitReached as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if entry is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"comment": entry}


@router.delete("/{task_id}/comments/{comment_id}")
async def delete_comment_route(
    task_id: str,
    comment_id: str,
    user: User = Depends(get_current_user),
):
    """Remove one comment by id. Idempotent: an unknown id returns deleted=false."""
    result = await delete_comment(_config, user.id, task_id, comment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"deleted": result}


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
        # Thread the user's tz like the reschedule route below — without it
        # the parser falls back to the hard-coded Madrid default and "today
        # 9am" lands wrong the moment the settings timezone diverges.
        from lazyclaw.tasks.timezone import get_user_tz
        draft = regex_parse_full(body.text, tz=await get_user_tz(_config, user.id))
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
    logger.debug(
        "[route:tasks] POST reschedule id=%s user=%s mode=%s",
        task_id, user.id, body.mode,
    )
    task = await get_task(_config, user.id, task_id)
    if not task:
        logger.warning(
            "[route:tasks] POST reschedule id=%s user=%s -> 404 task not found",
            task_id, user.id,
        )
        raise HTTPException(status_code=404, detail="Task not found")

    from lazyclaw.tasks.nl_time import parse as nl_parse
    from lazyclaw.tasks.timezone import get_user_tz

    user_tz = await get_user_tz(_config, user.id)

    if body.mode == "smart":
        suggested = await _smart_reschedule_phrase(_config, user.id, task)
        if not suggested:
            logger.warning(
                "[route:tasks] POST reschedule id=%s user=%s -> 503 smart worker offline",
                task_id, user.id,
            )
            raise HTTPException(
                status_code=503,
                detail="Smart reschedule unavailable — worker LLM offline.",
            )
        phrase_to_use = suggested
    else:
        phrase_to_use = (body.phrase or "").strip()
        if not phrase_to_use:
            logger.warning(
                "[route:tasks] POST reschedule id=%s user=%s -> 400 phrase required for mode=nl",
                task_id, user.id,
            )
            raise HTTPException(status_code=400, detail="Phrase is required for mode=nl")

    parsed = nl_parse(phrase_to_use, tz=user_tz)
    if not parsed.reminder_at and not parsed.due_date:
        logger.warning(
            "[route:tasks] POST reschedule id=%s user=%s -> 422 unparseable phrase",
            task_id, user.id,
        )
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
        # Do not interpolate exc — its message can echo the submitted
        # date/time value. Log only the route + user + a static reason.
        logger.warning(
            "[route:tasks] POST reschedule id=%s user=%s -> 400 update rejected (value validation)",
            task_id, user.id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        logger.warning(
            "[route:tasks] POST reschedule id=%s user=%s -> 404 task not found (post-update)",
            task_id, user.id,
        )
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
        logger.warning(
            "[route:tasks] smart reschedule worker LLM failed user=%s",
            user_id, exc_info=True,
        )
        return None
    raw = (response.content or "").strip() if response else ""
    # Strip quotes / a leading bullet — the parser is permissive but the
    # model sometimes wraps anyway.
    raw = raw.strip('"').strip("'").lstrip("-•·").strip()
    raw = raw.splitlines()[0].strip()
    return raw or None
