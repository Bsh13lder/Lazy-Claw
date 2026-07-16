"""Task storage: encrypted CRUD for the tasks table.

Follows the same pattern as lazyclaw.heartbeat.orchestrator for encryption
and lazyclaw.memory.personal for user-scoped data.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

ENCRYPTED_FIELDS = frozenset({"title", "description", "category", "tags", "steps"})

# Encrypted fields whose value is a JSON list rather than a plain string.
# ``create_task`` ``json.dumps`` these before encrypting; ``update_task`` must
# do the same, or ``encrypt()`` calls ``.encode`` on a ``list`` and raises
# (HTTP 500, edit silently lost).
_JSON_LIST_ENCRYPTED_FIELDS = frozenset({"tags", "steps"})

# A task category is promoted to a first-class project once this many tasks
# share it (casefold). Below the threshold it stays a phantom category.
_PROJECT_MATERIALIZE_THRESHOLD = 3

TASK_COLUMNS = [
    "id", "user_id", "title", "description", "category", "priority",
    "status", "owner", "due_date", "reminder_at", "reminder_job_id", "recurring",
    "tags", "nag_count", "created_at", "completed_at",
    # Execution tracking — saved-but-unexecuted gap fix
    "last_error", "attempt_count", "last_attempted_at",
    "trace_session_id", "lazybrain_note_id",
    # Sub-task checklist (encrypted JSON: [{id, title, done}])
    "steps",
    # Advance reminders — plaintext JSON list of ISO timestamps that
    # fire before reminder_at (e.g. T-2h, T-1h). Each timestamp pops
    # off the list once delivered.
    "pre_reminders",
    # Atomic nag claim (set on the conditional UPDATE in the heartbeat
    # daemon, cleared by stale-claim sweep after 5 minutes).
    "nag_fired_at",
    # Recurring offset — minutes between due_date and reminder_at,
    # stamped at create time. Recurring respawn uses this directly so
    # the offset can't drift on parse failure (the original silent-drift
    # bug). Null = "no offset", treat next reminder = next due.
    "reminder_offset_minutes",
    # Progress tracking — encrypted JSON timeline. Each entry is
    # {ts, kind, text, source}. Append-only via append_progress_entry.
    "progress_log",
    # Pulse cadence (cron expression) + template FK + state-machine
    # fields used by the heartbeat stale detector and pulse cooldown.
    "check_every",
    "progress_template_id",
    "nudge_sent_at",
    "last_pulse_fired_at",
    # Per-task budget allocation — a slice of the parent project's budget.
    # Plaintext REAL (same profile as projects.budget); nullable.
    "allocated_budget",
    # Offline-sync columns (feat/flutter-mobile) — plaintext timestamps so
    # the /changes delta feed can filter with a simple SQL comparison.
    # updated_at: bumped on every mutation (insert + every UPDATE path).
    # deleted_at: soft-delete tombstone; NULL = live, non-NULL = deleted.
    "updated_at",
    "deleted_at",
]

# progress_log is encrypted JSON — same envelope as tags/steps. Adding
# it to ENCRYPTED_FIELDS would route it through decrypt_field, but the
# JSON shape is more useful unwrapped lazily by callers.
_PROGRESS_LOG_AAD = "task:progress_log"

TASK_SELECT = ", ".join(TASK_COLUMNS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encrypt_field(value: str | None, key: bytes) -> str | None:
    if value is None:
        return None
    return encrypt(value, key)


def _row_to_dict(row, key: bytes) -> dict:
    result = {}
    for i, col in enumerate(TASK_COLUMNS):
        value = row[i]
        if col in ENCRYPTED_FIELDS:
            value = decrypt_field(value, key)
        result[col] = value
    return result


def _validate_iso_dt(value: str | None, field_name: str) -> str | None:
    """Validate that ``value`` is a parseable ISO-8601 datetime string.

    Used at write time so malformed ``reminder_at`` / ``pre_reminders``
    entries can't reach the heartbeat daemon and silently stop firing.
    Returns the trimmed value on success, raises ``ValueError`` on bad
    input. None / empty pass through.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 string, got {type(value).__name__}")
    trimmed = value.strip()
    try:
        datetime.fromisoformat(trimmed)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"{field_name}={trimmed!r} is not a parseable ISO-8601 datetime"
        ) from exc
    return trimmed


def _compute_reminder_offset_minutes(
    due_date: str | None, reminder_at: str | None,
) -> int | None:
    """Compute minutes between due_date (date or datetime) and reminder_at.

    Returns None when either value is missing or unparseable. Stored on
    create so the recurring respawn path doesn't have to re-derive it
    by parsing fields that may have been mutated since.
    """
    if not due_date or not reminder_at:
        return None
    try:
        # ``due_date`` may be a bare YYYY-MM-DD or a full datetime.
        if "T" in due_date:
            due_dt = datetime.fromisoformat(due_date)
        else:
            due_dt = datetime.fromisoformat(f"{due_date}T00:00:00")
        rem_dt = datetime.fromisoformat(reminder_at)
    except (ValueError, TypeError):
        return None
    if due_dt.tzinfo is None:
        due_dt = due_dt.replace(tzinfo=timezone.utc)
    if rem_dt.tzinfo is None:
        rem_dt = rem_dt.replace(tzinfo=timezone.utc)
    delta = rem_dt - due_dt
    return int(delta.total_seconds() // 60)


_VALID_PROGRESS_KINDS = frozenset({
    "started", "working", "progress", "stuck", "blocked",
    "paused", "resumed", "done", "pulse_fired",
})

_VALID_PROGRESS_SOURCES = frozenset({"nl", "pulse", "button", "manual", "auto"})


def _normalize_progress_kind(kind: str | None) -> str:
    """Map free-form kind input onto the canonical set; default 'progress'."""
    k = (kind or "progress").strip().lower()
    return k if k in _VALID_PROGRESS_KINDS else "progress"


def _normalize_progress_source(source: str | None) -> str:
    s = (source or "manual").strip().lower()
    return s if s in _VALID_PROGRESS_SOURCES else "manual"


def _normalize_steps(steps: list[dict] | None) -> list[dict]:
    """Coerce a steps payload into the canonical shape [{id, title, done}].

    Accepts loose input (plain strings, partial dicts) so callers — including
    the NL parser and the AI quick-add — don't need to pre-shape everything.
    """
    if not steps:
        return []
    out: list[dict] = []
    for i, raw in enumerate(steps):
        if isinstance(raw, str):
            title = raw.strip()
            if not title:
                continue
            out.append({"id": f"s{i}-{uuid4().hex[:6]}", "title": title, "done": False})
            continue
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title", "")).strip()
        if not title:
            continue
        out.append({
            "id": str(raw.get("id") or f"s{i}-{uuid4().hex[:6]}"),
            "title": title,
            "done": bool(raw.get("done", False)),
        })
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_task(
    config: Config,
    user_id: str,
    title: str,
    description: str | None = None,
    category: str | None = None,
    priority: str = "medium",
    owner: str = "user",
    due_date: str | None = None,
    reminder_at: str | None = None,
    recurring: str | None = None,
    tags: list[str] | None = None,
    trace_session_id: str | None = None,
    steps: list[dict] | None = None,
    pre_reminders: list[str] | None = None,
    task_id: str | None = None,
) -> dict:
    """Create a new task. Returns the full task dict (decrypted).

    ``trace_session_id`` lets us link a failed task back to the conversation
    trace that created it — useful when reviewing unexecuted work.

    ``steps`` is an optional list of sub-task dicts shaped ``{id, title, done}``.
    IDs are auto-assigned when missing.
    """
    # Strict validation up-front: malformed timestamps used to slip
    # through and silently stop firing at heartbeat-tick time. Raise so
    # the API surface returns a 400 with a real reason instead of the
    # row landing in the table and never being nagged.
    reminder_at = _validate_iso_dt(reminder_at, "reminder_at")
    if pre_reminders:
        validated_pre: list[str] = []
        for entry in pre_reminders:
            cleaned = _validate_iso_dt(entry, "pre_reminders[entry]")
            if cleaned:
                validated_pre.append(cleaned)
        pre_reminders = validated_pre or None

    key = await get_user_dek(config, user_id)
    task_id = task_id or str(uuid4())

    # Idempotent replay: if the client-minted id already exists for this user,
    # return the existing row without inserting a duplicate.
    async with db_session(config) as db:
        cur = await db.execute(
            f"SELECT {TASK_SELECT} FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
        existing_row = await cur.fetchone()
    if existing_row is not None:
        return _row_to_dict(existing_row, key)

    enc_title = encrypt(title, key)
    enc_description = _encrypt_field(description, key)
    enc_category = _encrypt_field(category, key)
    enc_tags = _encrypt_field(json.dumps(tags), key) if tags else None

    # Normalize + encrypt steps payload.
    normalized_steps = _normalize_steps(steps) if steps else None
    enc_steps = (
        _encrypt_field(json.dumps(normalized_steps), key)
        if normalized_steps else None
    )

    # Create a reminder job if reminder_at is set
    reminder_job_id = None
    if reminder_at:
        reminder_job_id = await _create_reminder_job(
            config, user_id, title, reminder_at, task_id
        )
        # Auto-set due_date from reminder_at if not provided
        if not due_date:
            due_date = reminder_at[:10]

    # Stamp the offset once at create so the recurring respawn path uses
    # a deterministic value instead of re-parsing fields that may drift.
    reminder_offset_minutes = _compute_reminder_offset_minutes(due_date, reminder_at)

    created_at = datetime.now(timezone.utc).isoformat()
    # On insert, updated_at == created_at (no mutations have occurred yet).
    updated_at = created_at

    # Mirror into LazyBrain first so we can record the note id against the task row.
    # Fire-and-forget on failure so task creation never blocks on PKM problems.
    lazybrain_note_id: str | None = None
    try:
        from lazyclaw.lazybrain import events as lb_events
        from lazyclaw.lazybrain import store as lb_store

        body_parts: list[str] = [f"**Task:** {title}"]
        if description:
            body_parts.append(description)
        meta_bits: list[str] = [f"priority `{priority}`"]
        if due_date:
            meta_bits.append(f"due `{due_date}`")
        if reminder_at:
            meta_bits.append(f"reminder `{reminder_at}`")
        if recurring:
            meta_bits.append(f"recurring `{recurring}`")
        body_parts.append("— " + " · ".join(meta_bits))
        body = "\n\n".join(body_parts)

        lb_tags = ["task", "auto", f"priority/{priority}"]
        lb_tags.append(f"owner/{'user' if owner == 'user' else 'agent'}")
        if category:
            lb_tags.append(f"category/{category}")
        for t in tags or []:
            lb_tags.append(str(t))

        importance_map = {"urgent": 9, "high": 7, "medium": 5, "low": 3}
        note = await lb_store.save_note(
            config,
            user_id,
            content=body,
            title=f"Task: {title}",
            tags=lb_tags,
            importance=importance_map.get(priority, 5),
        )
        lazybrain_note_id = note["id"]
        lb_events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"], source="task",
        )
    except Exception:
        logger.warning(
            "lazybrain task mirror failed for task %s (user=%s)",
            task_id, user_id, exc_info=True,
        )

    pre_reminders_json = (
        json.dumps(sorted(set(pre_reminders))) if pre_reminders else None
    )

    placeholders = ", ".join(["?"] * len(TASK_COLUMNS))
    async with db_session(config) as db:
        await db.execute(
            f"INSERT INTO tasks ({TASK_SELECT}) VALUES ({placeholders})",
            (
                task_id, user_id, enc_title, enc_description, enc_category,
                priority, "todo", owner, due_date, reminder_at, reminder_job_id,
                recurring, enc_tags, 0,
                created_at, None,
                None, 0, None, trace_session_id, lazybrain_note_id,
                enc_steps,
                pre_reminders_json,
                None,  # nag_fired_at — set atomically by daemon on first nag
                reminder_offset_minutes,
                None,  # progress_log — populated by append_progress_entry
                None,  # check_every — set when user opts in to pulses
                None,  # progress_template_id
                None,  # nudge_sent_at
                None,  # last_pulse_fired_at
                None,  # allocated_budget — opted into via update_task
                updated_at,  # updated_at == created_at on insert
                None,  # deleted_at — NULL until delete_task is called
            ),
        )
        await db.commit()

    logger.debug("Created task %s (%s) for user %s", task_id, owner, user_id)

    # Promote the category to a real project once it's proven itself (≥N tasks).
    # Guarded + best-effort — task creation never fails on PKM/budget problems.
    await _maybe_materialize_project(config, user_id, category)

    return {
        "id": task_id, "user_id": user_id, "title": title,
        "description": description, "category": category,
        "priority": priority, "status": "todo", "owner": owner,
        "due_date": due_date,
        "reminder_at": reminder_at, "reminder_job_id": reminder_job_id,
        "recurring": recurring, "tags": json.dumps(tags) if tags else None,
        "nag_count": 0, "created_at": created_at,
        "completed_at": None,
        "last_error": None, "attempt_count": 0, "last_attempted_at": None,
        "trace_session_id": trace_session_id,
        "lazybrain_note_id": lazybrain_note_id,
        "steps": json.dumps(normalized_steps) if normalized_steps else None,
        "pre_reminders": pre_reminders_json,
        "nag_fired_at": None,
        "reminder_offset_minutes": reminder_offset_minutes,
        "progress_log": None,
        "check_every": None,
        "progress_template_id": None,
        "nudge_sent_at": None,
        "last_pulse_fired_at": None,
        "allocated_budget": None,
        "updated_at": updated_at,
        "deleted_at": None,
    }


async def list_tasks(
    config: Config,
    user_id: str,
    status: str | None = None,
    priority: str | None = None,
    bucket: str | None = None,
    owner: str | None = None,
) -> list[dict]:
    """List tasks with optional filters.

    bucket: "today" | "upcoming" | "someday" | None (all)
    owner: "user" | "agent" | None (all)
    """
    key = await get_user_dek(config, user_id)
    today_str = date.today().isoformat()

    where_clauses = ["user_id = ?", "deleted_at IS NULL"]
    params: list = [user_id]

    if owner:
        where_clauses.append("owner = ?")
        params.append(owner)

    if status and status != "all":
        where_clauses.append("status = ?")
        params.append(status)

    if priority:
        where_clauses.append("priority = ?")
        params.append(priority)

    if bucket == "today":
        where_clauses.append("(due_date <= ? OR due_date IS NULL)")
        where_clauses.append("status IN ('todo', 'in_progress')")
        params.append(today_str)
    elif bucket == "upcoming":
        where_clauses.append("due_date > ?")
        where_clauses.append("status IN ('todo', 'in_progress')")
        params.append(today_str)
    elif bucket == "someday":
        where_clauses.append("due_date IS NULL")
        where_clauses.append("status IN ('todo', 'in_progress')")

    where = " AND ".join(where_clauses)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {TASK_SELECT} FROM tasks WHERE {where} "
            "ORDER BY "
            "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 ELSE 3 END, "
            "due_date ASC NULLS LAST, created_at DESC",
            params,
        )
        rows = await cursor.fetchall()

    return [_row_to_dict(row, key) for row in rows]


async def get_task(
    config: Config, user_id: str, task_id: str
) -> dict | None:
    """Get a single task by ID."""
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {TASK_SELECT} FROM tasks "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (task_id, user_id),
        )
        row = await cursor.fetchone()

    return _row_to_dict(row, key) if row else None


async def find_task_id_by_note(
    config: Config, user_id: str, note_id: str,
) -> str | None:
    """Resolve a LazyBrain note id back to its mirrored task id, if any.

    LazyBrain notes auto-mirror tasks via ``lazybrain_note_id`` on the tasks
    table — this is the inverse lookup used by the web UI's sidebar
    "tick to complete" interaction.
    """
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT id FROM tasks WHERE lazybrain_note_id = ? AND user_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (note_id, user_id),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


async def get_task_owner(
    config: Config, task_id: str
) -> str | None:
    """Return the user_id that owns a task, or None if the task doesn't exist.

    Used by channel callbacks (Telegram buttons) where the callback itself
    doesn't know which user owns a task — only the task_id comes back from
    the inline button's callback_data.
    """
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT user_id FROM tasks WHERE id = ?", (task_id,),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


async def get_nagging_tasks(
    config: Config, user_id: str | None = None, limit: int = 5,
) -> list[dict]:
    """Return tasks currently being nagged (reminder due + nag_count > 0).

    If `user_id` is None, returns nagging tasks across all users — used by
    channel adapters resolving a natural-language "done" against whichever
    user owns the current open reminder.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    if user_id:
        params = (user_id, now_iso)
        where = (
            "WHERE user_id = ? AND status IN ('todo', 'in_progress') "
            "AND deleted_at IS NULL "
            "AND reminder_at IS NOT NULL AND reminder_at <= ? "
            "AND nag_count > 0"
        )
    else:
        params = (now_iso,)
        where = (
            "WHERE status IN ('todo', 'in_progress') "
            "AND deleted_at IS NULL "
            "AND reminder_at IS NOT NULL AND reminder_at <= ? "
            "AND nag_count > 0"
        )

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {TASK_SELECT} FROM tasks {where} "
            f"ORDER BY reminder_at DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()

    # Decrypt per row using each owner's key (may differ across rows).
    results: list[dict] = []
    keys: dict[str, bytes] = {}
    for row in rows:
        owner_uid = row[TASK_COLUMNS.index("user_id")]
        if owner_uid not in keys:
            try:
                keys[owner_uid] = await get_user_dek(config, owner_uid)
            except Exception:
                logger.debug("Could not derive DEK for user %s", owner_uid, exc_info=True)
                continue
        results.append(_row_to_dict(row, keys[owner_uid]))
    return results


async def list_categories(config: Config, user_id: str) -> list[str]:
    """Return the user's distinct task categories (decrypted), preserving the
    first-seen casing. These are the "projects" auto-detected from tasks even
    before a real ``projects`` row exists.

    Tombstone-aware: ``list_tasks`` filters ``deleted_at IS NULL``, so
    categories that only exist on soft-deleted tasks don't resurface.
    """
    tasks = await list_tasks(config, user_id, status="all")
    seen: set[str] = set()
    out: list[str] = []
    for t in tasks:
        cat = (t.get("category") or "").strip()
        if not cat:
            continue
        key = cat.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cat)
    return out


async def count_tasks_in_category(
    config: Config, user_id: str, category: str
) -> int:
    """Count the user's live tasks whose category matches ``category`` (casefold).

    Categories are encrypted at rest, so this decrypts + matches in Python
    rather than via a SQL ``WHERE``. Soft-deleted tasks are excluded by
    ``list_tasks``'s tombstone filter.
    """
    target = (category or "").strip().casefold()
    if not target:
        return 0
    tasks = await list_tasks(config, user_id, status="all")
    return sum(
        1 for t in tasks if (t.get("category") or "").strip().casefold() == target
    )


async def rename_category(
    config: Config, user_id: str, old_name: str, new_name: str
) -> int:
    """Re-point every live task tagged with ``old_name`` to ``new_name``.

    Categories are encrypted, so this can't be a single SQL UPDATE — we find
    the matching tasks (casefold compare) and re-encrypt each. Keeps the
    ``tasks.category`` ⇄ ``projects.name_key`` join consistent after a project
    rename. Each re-point goes through ``update_task`` so the offline-sync
    ``updated_at`` stamp is bumped and clients pick up the change on the next
    delta pull. Returns the number of tasks updated. A no-op (returns 0) when
    the names are equivalent or ``old_name`` has no tasks.
    """
    old_key = (old_name or "").strip().casefold()
    new_clean = (new_name or "").strip()
    if not old_key or not new_clean or old_key == new_clean.casefold():
        return 0
    tasks = await list_tasks(config, user_id, status="all")
    updated = 0
    for t in tasks:
        if (t.get("category") or "").strip().casefold() == old_key:
            await update_task(config, user_id, t["id"], category=new_clean)
            updated += 1
    return updated


async def _maybe_materialize_project(
    config: Config, user_id: str, category: str | None
) -> None:
    """Promote a task category to a first-class project once it crosses the
    materialization threshold.

    Fire-and-forget: never raises, never blocks task creation. No-op when the
    category is empty, below threshold, or already backed by a ``projects`` row.
    """
    cat = (category or "").strip()
    if not cat:
        return
    try:
        from lazyclaw.budgets import store as budget_store

        existing = await budget_store.get_project_by_name(config, user_id, cat)
        if existing is not None:
            return
        count = await count_tasks_in_category(config, user_id, cat)
        if count < _PROJECT_MATERIALIZE_THRESHOLD:
            return
        await budget_store.create_project(config, user_id, cat)
        logger.debug(
            "Materialized project %r for user %s (%d tasks)", cat, user_id, count
        )
    except Exception:
        logger.debug(
            "project materialization skipped for %r", category, exc_info=True
        )


async def update_task(
    config: Config,
    user_id: str,
    task_id: str,
    **fields,
) -> bool:
    """Update task fields. Encrypts sensitive fields. Manages reminder lifecycle."""
    if not fields:
        return False

    key = await get_user_dek(config, user_id)
    set_clauses: list[str] = []
    params: list = []

    # Strict validation: ``reminder_at`` must be parseable, every entry in
    # ``pre_reminders`` must be parseable. Reject the whole update on the
    # first invalid value — better than silent stop-firing later.
    if "reminder_at" in fields and fields["reminder_at"] is not None:
        fields["reminder_at"] = _validate_iso_dt(fields["reminder_at"], "reminder_at")
    if "pre_reminders" in fields and fields["pre_reminders"] is not None:
        raw_list = fields["pre_reminders"]
        if isinstance(raw_list, str):
            try:
                raw_list = json.loads(raw_list)
            except (json.JSONDecodeError, TypeError):
                raise ValueError("pre_reminders JSON string is not a valid array")
        if not isinstance(raw_list, list):
            raise ValueError("pre_reminders must be a list of ISO-8601 strings")
        cleaned: list[str] = []
        for entry in raw_list:
            v = _validate_iso_dt(entry, "pre_reminders[entry]")
            if v:
                cleaned.append(v)
        fields["pre_reminders"] = cleaned

    for field_name, value in fields.items():
        if field_name in ENCRYPTED_FIELDS and value is not None:
            # List-shaped encrypted fields (tags/steps) are stored as a JSON
            # string — mirror create_task: json.dumps before encrypting.
            # ``steps`` is normalized to the canonical {id,title,done} shape.
            if field_name in _JSON_LIST_ENCRYPTED_FIELDS and isinstance(value, list):
                if field_name == "steps":
                    value = _normalize_steps(value)
                value = json.dumps(value)
            value = encrypt(value, key)
        elif field_name == "pre_reminders" and value is not None:
            # Accept either a list of ISO strings (preferred) or a pre-encoded
            # JSON string. Empty list collapses to NULL so the heartbeat scan
            # filter (`pre_reminders != '[]' AND IS NOT NULL`) skips the row.
            if isinstance(value, list):
                value = json.dumps(sorted(set(value))) if value else None
        set_clauses.append(f"{field_name} = ?")
        params.append(value)

    # Auto-manage reminder job when reminder_at changes
    if "reminder_at" in fields:
        new_reminder = fields["reminder_at"]
        task = await get_task(config, user_id, task_id)
        if task:
            old_job_id = task.get("reminder_job_id")
            if old_job_id:
                await _delete_reminder_job(config, user_id, old_job_id)
            if new_reminder:
                job_id = await _create_reminder_job(
                    config, user_id, task["title"], new_reminder, task_id
                )
                set_clauses.append("reminder_job_id = ?")
                params.append(job_id)
            else:
                set_clauses.append("reminder_job_id = ?")
                params.append(None)
            logger.debug(
                "[tasks] reminder rescheduled task=%s user=%s new_reminder=%s",
                task_id, user_id, new_reminder,
            )

    # Keep reminder_offset_minutes in sync when due_date or reminder_at
    # change. The recurring respawn path reads this directly, so a stale
    # offset would re-introduce the silent-drift bug.
    if "reminder_at" in fields or "due_date" in fields:
        # Re-fetch in case only one of the two fields was supplied.
        latest = await get_task(config, user_id, task_id)
        if latest:
            new_due = fields.get("due_date", latest.get("due_date"))
            new_rem = fields.get("reminder_at", latest.get("reminder_at"))
            offset = _compute_reminder_offset_minutes(new_due, new_rem)
            set_clauses.append("reminder_offset_minutes = ?")
            params.append(offset)

            # Re-derive advance ``pre_reminders`` against the NEW reminder time
            # so a reschedule doesn't leave them pinned to the old instant (T4).
            # Skip when the caller passed ``pre_reminders`` explicitly (its value
            # was already appended above and wins verbatim). Clearing
            # ``reminder_at`` resolves to [] → NULL, dropping stale advance nags.
            if "pre_reminders" not in fields:
                from lazyclaw.tasks.pre_reminders import resolve_pre_reminders

                recomputed = await resolve_pre_reminders(
                    config, user_id, reminder_at=new_rem, explicit=None,
                )
                set_clauses.append("pre_reminders = ?")
                params.append(json.dumps(sorted(set(recomputed))) if recomputed else None)

    # Reset nag state on any reminder_at change so the user gets a fresh
    # escalation series instead of jumping mid-sequence.
    if "reminder_at" in fields:
        set_clauses.append("nag_count = ?")
        params.append(0)
        set_clauses.append("nag_fired_at = ?")
        params.append(None)

    # Auto-set completed_at when status becomes done
    if fields.get("status") == "done" and "completed_at" not in fields:
        set_clauses.append("completed_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())

    # Always bump updated_at on any mutation so offline clients can delta-sync.
    set_clauses.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())

    params.extend([task_id, user_id])

    async with db_session(config) as db:
        result = await db.execute(
            f"UPDATE tasks SET {', '.join(set_clauses)} "
            "WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()

    # Mirror any status transition to the LazyBrain note so failures/cancels
    # don't leave the brain thinking the task is still pending.
    if "status" in fields:
        task = await get_task(config, user_id, task_id)
        if task:
            await _mirror_status_to_lazybrain(
                config, user_id, task, fields["status"],
                error=fields.get("last_error"),
            )

    return result.rowcount > 0


async def fail_task(
    config: Config,
    user_id: str,
    task_id: str,
    error: str,
) -> bool:
    """Mark a task as failed, record the error, bump the attempt count.

    Separate from `cancelled` — failed means *tried and didn't work*, cancelled
    means *no longer needed*. Failure state is visible in LazyBrain (❌ FAILED
    prefix on the mirrored note) so the user sees unexecuted work in their
    second brain, not silently-abandoned rows in the DB.
    """
    task = await get_task(config, user_id, task_id)
    if not task:
        return False

    now = datetime.now(timezone.utc).isoformat()
    attempts = int(task.get("attempt_count") or 0) + 1

    async with db_session(config) as db:
        result = await db.execute(
            "UPDATE tasks SET status = 'failed', last_error = ?, "
            "attempt_count = ?, last_attempted_at = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (error, attempts, now, now, task_id, user_id),
        )
        await db.commit()

    task["status"] = "failed"
    task["last_error"] = error
    task["attempt_count"] = attempts
    task["last_attempted_at"] = now
    # Log ids + attempt count only — never the raw error text (may carry PII).
    logger.warning(
        "[tasks] task %s marked FAILED (user=%s attempt=%d)",
        task_id, user_id, attempts,
    )
    await _mirror_status_to_lazybrain(
        config, user_id, task, "failed", error=error,
    )
    return result.rowcount > 0


_STATUS_BADGE = {
    "done":       ("✅ DONE",       "done"),
    "cancelled":  ("🚫 CANCELLED",  "cancelled"),
    "failed":     ("❌ FAILED",     "failed"),
    "in_progress":("⏳ IN PROGRESS","in-progress"),
    "todo":       ("📝 TODO",       "pending"),
}


def _build_task_mirror_body(task: dict) -> str:
    """Rebuild a LazyBrain note body from a task row. Shared by create and heal."""
    body_parts: list[str] = [f"**Task:** {task.get('title') or '(untitled)'}"]
    desc = task.get("description")
    if desc:
        body_parts.append(desc)
    meta_bits: list[str] = [f"priority `{task.get('priority') or 'medium'}`"]
    if task.get("due_date"):
        meta_bits.append(f"due `{task['due_date']}`")
    if task.get("reminder_at"):
        meta_bits.append(f"reminder `{task['reminder_at']}`")
    if task.get("recurring"):
        meta_bits.append(f"recurring `{task['recurring']}`")
    if task.get("completed_at"):
        meta_bits.append(f"completed `{task['completed_at']}`")
    body_parts.append("— " + " · ".join(meta_bits))
    if task.get("last_error"):
        body_parts.append(f"**Last error:** {task['last_error']}")
    return "\n\n".join(body_parts)


def _build_task_mirror_tags(task: dict) -> list[str]:
    """Rebuild the base tag list for a task's mirror note. No status/* tag —
    that is appended by the caller so it stays canonical across paths."""
    owner = task.get("owner") or "user"
    priority = task.get("priority") or "medium"
    tags: list[str] = [
        "task", "auto",
        f"priority/{priority}",
        f"owner/{'user' if owner == 'user' else 'agent'}",
    ]
    if task.get("category"):
        tags.append(f"category/{task['category']}")
    raw_tags = task.get("tags")
    if raw_tags:
        # task row stores tags as a JSON string (encrypted → decrypted). Best-effort parse.
        try:
            parsed = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
            for t in parsed or []:
                tags.append(str(t))
        except Exception:
            logger.debug(
                "malformed task tags json on task %s", task.get("id"),
                exc_info=True,
            )
    return tags


async def _mirror_status_to_lazybrain(
    config: Config,
    user_id: str,
    task: dict,
    new_status: str,
    error: str | None = None,
) -> None:
    """Stamp the mirrored LazyBrain note with the new status.

    If the task has no `lazybrain_note_id` (create-time mirror failed silently,
    or the user deleted the note manually), this heals retroactively by
    creating a fresh mirror carrying the new status — PKM integrity is the
    product, so a missing note is fixed on next status change instead of
    silently dropped. Still fire-and-forget on exception.
    """
    try:
        from lazyclaw.lazybrain import events as lb_events
        from lazyclaw.lazybrain import store as lb_store

        badge, status_tag = _STATUS_BADGE.get(
            new_status, (f"• {new_status.upper()}", new_status),
        )
        header = f"{badge} —"
        if error:
            header += f" {error}"

        note_id = task.get("lazybrain_note_id")
        note = await lb_store.get_note(config, user_id, note_id) if note_id else None

        if note is None:
            # Heal path — note missing or never created. Build fresh mirror
            # with the current status already baked in, then persist the new
            # note id against the task row so future transitions use the
            # update path below.
            base_body = _build_task_mirror_body(task)
            new_body = f"{header}\n\n{base_body}"
            tags = _build_task_mirror_tags(task)
            tags.append(f"status/{status_tag}")

            importance_map = {"urgent": 9, "high": 7, "medium": 5, "low": 3}
            importance = importance_map.get(task.get("priority") or "medium", 5)

            created = await lb_store.save_note(
                config, user_id,
                content=new_body,
                title=f"Task: {task.get('title') or '(untitled)'}",
                tags=tags,
                importance=importance,
            )
            async with db_session(config) as db:
                await db.execute(
                    "UPDATE tasks SET lazybrain_note_id = ? "
                    "WHERE id = ? AND user_id = ?",
                    (created["id"], task.get("id"), user_id),
                )
                await db.commit()
            task["lazybrain_note_id"] = created["id"]
            lb_events.publish_note_saved(
                user_id, created["id"], created.get("title"),
                created.get("tags"), source="task",
            )
            logger.info(
                "lazybrain mirror healed for task %s (user=%s) — created "
                "note %s on status transition to %s",
                task.get("id"), user_id, created["id"], new_status,
            )
            return

        # Normal path — note exists, update in place.
        body = note.get("content") or ""
        # Strip any prior status badge so we don't stack them on repeated transitions.
        for known_badge, _ in _STATUS_BADGE.values():
            if body.startswith(f"{known_badge} —\n\n"):
                body = body[len(f"{known_badge} —\n\n"):]
                break
        # Also strip a prior badge that already carries an error payload
        # (e.g. `❌ FAILED — timeout\n\n...`) so we don't accidentally keep
        # the old error in the body when the new status provides a new one.
        for known_badge, _ in _STATUS_BADGE.values():
            prefix = f"{known_badge} — "
            if body.startswith(prefix):
                nl = body.find("\n\n")
                if nl != -1:
                    body = body[nl + 2:]
                break

        new_body = f"{header}\n\n{body}"

        # Refresh tags: drop any prior status/* tag, add the new one.
        old_tags = note.get("tags") or []
        new_tags = [t for t in old_tags if not t.startswith("status/")]
        new_tags.append(f"status/{status_tag}")

        updated = await lb_store.update_note(
            config, user_id, note_id,
            content=new_body, tags=new_tags,
        )
        if updated:
            lb_events.publish_note_saved(
                user_id, updated["id"], updated.get("title"),
                updated.get("tags"), source="task",
            )
    except Exception:
        logger.warning(
            "lazybrain status mirror failed for task %s (user=%s, status=%s)",
            task.get("id"), user_id, new_status, exc_info=True,
        )


async def complete_task(
    config: Config, user_id: str, task_id: str
) -> bool:
    """Mark task done. Deletes reminder job. Handles recurring (creates next).

    Writes a ``done`` entry to the progress log + clears ``nudge_sent_at``
    so the timeline closes cleanly and any future occurrence of the same
    title (recurring) starts with a fresh nudge state machine.
    """
    task = await get_task(config, user_id, task_id)
    if not task:
        return False

    # Idempotency guard: completing an already-done task must be a no-op.
    # Mobile sync is at-least-once (a dropped response re-pushes the complete
    # op) and a web double-click both fire this twice. Without the guard the
    # recurring-respawn block below runs again and spawns a DUPLICATE next
    # occurrence. Return True so the caller still sees success.
    if task.get("status") == "done":
        return True

    # Delete reminder job if exists
    if task.get("reminder_job_id"):
        await _delete_reminder_job(config, user_id, task["reminder_job_id"])

    # Pause any active pulse job so the daemon doesn't fire on a done
    # task before it gets the orchestrator-side pause from _fire_task_pulse.
    try:
        from lazyclaw.heartbeat.orchestrator import list_jobs, pause_job
        jobs = await list_jobs(config, user_id)
        prefix = f"[PULSE:{task_id}:"
        for j in jobs:
            if (j.get("instruction") or "").startswith(prefix):
                await pause_job(config, user_id, j["id"])
    except Exception:
        logger.debug("complete_task: could not pause pulses", exc_info=True)

    # Timeline: log a done entry before flipping status so the
    # progress_log is the canonical "what happened" record.
    try:
        await append_progress_entry(
            config, user_id, task_id,
            kind="done", source="auto",
        )
    except Exception:
        logger.debug("complete_task: progress entry failed", exc_info=True)

    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE tasks SET status = 'done', completed_at = ?, "
            "reminder_job_id = NULL, nag_count = 0, "
            "nudge_sent_at = NULL, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (now, now, task_id, user_id),
        )
        await db.commit()

    # Keep LazyBrain mirror honest — stamp note with ✅ DONE.
    # Refresh in-memory task fields the mirror body builder reads on the
    # heal path so the brain note carries the completion timestamp.
    task["status"] = "done"
    task["completed_at"] = now
    await _mirror_status_to_lazybrain(config, user_id, task, "done")

    # Link the completed task into today's journal so it shows up as a
    # graph edge from the today node. Fire-and-forget — never blocks the
    # task-complete reply on PKM bookkeeping.
    try:
        from lazyclaw.lazybrain import journal as _lb_journal
        from lazyclaw.runtime.aio_helpers import fire_and_forget
        fire_and_forget(
            _lb_journal.link_note_today(
                config, user_id,
                title=f"Task: {task.get('title', '')}",
                kind="done",
            ),
            name=f"journal-link-task-done-{task_id}",
        )
    except Exception:
        logger.debug("journal link for completed task failed", exc_info=True)

    # Recurring: create the next occurrence using the stored offset.
    # The original parse-and-fallback path silently dropped the offset on
    # any parse error, so reminders drifted to bare next_due over time.
    # We now use ``reminder_offset_minutes`` (stamped at create) directly.
    if task.get("recurring"):
        try:
            from lazyclaw.heartbeat.cron import get_next_run
            # get_next_run returns a tz-aware (UTC) datetime, not a string.
            next_due = get_next_run(task["recurring"])
            next_date = next_due.date().isoformat() if next_due else None

            next_reminder = None
            offset_min = task.get("reminder_offset_minutes")
            if next_due is not None and offset_min is not None:
                try:
                    next_due_dt = next_due
                    if next_due_dt.tzinfo is None:
                        next_due_dt = next_due_dt.replace(tzinfo=timezone.utc)
                    next_reminder = (
                        next_due_dt + timedelta(minutes=int(offset_min))
                    ).isoformat()
                except (ValueError, TypeError):
                    # Should never happen — get_next_run always emits ISO
                    # — but if it does, surface loudly instead of silently
                    # collapsing the reminder onto next_due.
                    logger.warning(
                        "Recurring respawn: could not apply offset_min=%s to "
                        "next_due=%r for task %s — leaving next_reminder=None",
                        offset_min, next_due, task_id,
                    )

            tags = None
            if task.get("tags"):
                try:
                    tags = json.loads(task["tags"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Could not parse tags JSON for task %r; skipping tags", task.get("title"))

            new_task = await create_task(
                config, user_id,
                title=task["title"],
                description=task.get("description"),
                category=task.get("category"),
                priority=task.get("priority", "medium"),
                owner=task.get("owner", "user"),
                due_date=next_date,
                reminder_at=next_reminder,
                recurring=task["recurring"],
                tags=tags,
            )
            logger.debug(
                "[tasks] recurring respawn %s -> %s (next_due=%s user=%s)",
                task_id, new_task.get("id"), next_date, user_id,
            )
        except Exception:
            logger.warning(
                "[tasks] recurring respawn failed for task %s (user=%s)",
                task_id, user_id, exc_info=True,
            )

    return True


async def delete_task(
    config: Config, user_id: str, task_id: str
) -> bool:
    """Soft-delete a task: sets deleted_at + updated_at instead of removing the row.

    The row is preserved so the offline-sync /changes delta feed can inform
    clients that the task was deleted. Normal list_tasks/get_task queries
    filter out deleted rows (deleted_at IS NULL). Returns True when the task
    was found and marked deleted; False when it didn't exist (already deleted
    rows are also treated as not found so this is idempotent).
    """
    # Look up including already-deleted rows so we can still cancel the
    # reminder job even if the row was previously soft-deleted.
    async with db_session(config) as db:
        cur = await db.execute(
            f"SELECT {TASK_SELECT} FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
        raw_row = await cur.fetchone()

    if raw_row is None:
        return False

    key = await get_user_dek(config, user_id)
    task = _row_to_dict(raw_row, key)

    # If already soft-deleted, nothing more to do.
    if task.get("deleted_at") is not None:
        return False

    if task.get("reminder_job_id"):
        await _delete_reminder_job(config, user_id, task["reminder_job_id"])

    now = datetime.now(timezone.utc).isoformat()
    async with db_session(config) as db:
        result = await db.execute(
            "UPDATE tasks SET deleted_at = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (now, now, task_id, user_id),
        )
        await db.commit()
    if result.rowcount:
        logger.debug("[tasks] soft-deleted task %s (user=%s)", task_id, user_id)
    return result.rowcount > 0


# ---------------------------------------------------------------------------
# Reminder job helpers
# ---------------------------------------------------------------------------

async def _create_reminder_job(
    config: Config,
    user_id: str,
    title: str,
    reminder_at: str,
    task_id: str,
) -> str:
    """Create a heartbeat reminder job linked to a task."""
    from lazyclaw.heartbeat.orchestrator import create_job

    job_id = await create_job(
        config, user_id,
        name=f"Task: {title[:50]}",
        instruction=f"[TASK_REMINDER:{task_id}] {title}",
        job_type="reminder",
        context=reminder_at,
    )
    # Set next_run to the exact reminder time
    async with db_session(config) as db:
        await db.execute(
            "UPDATE agent_jobs SET next_run = ? WHERE id = ?",
            (reminder_at, job_id),
        )
        await db.commit()

    logger.debug(
        "[tasks] reminder job %s created for task %s (fires_at=%s user=%s)",
        job_id, task_id, reminder_at, user_id,
    )
    return job_id


async def _delete_reminder_job(
    config: Config, user_id: str, job_id: str
) -> None:
    """Delete a reminder job, ignoring errors."""
    try:
        from lazyclaw.heartbeat.orchestrator import delete_job
        await delete_job(config, user_id, job_id)
    except Exception:
        logger.debug("Failed to delete reminder job %s", job_id, exc_info=True)


# ---------------------------------------------------------------------------
# Sub-task steps — encrypted JSON checklist attached to each task.
# ---------------------------------------------------------------------------

def _parse_steps(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


async def set_steps(
    config: Config,
    user_id: str,
    task_id: str,
    steps: list[dict],
) -> list[dict] | None:
    """Replace the full sub-task checklist for a task.

    Returns the normalized list, or None if the task does not exist.
    """
    task = await get_task(config, user_id, task_id)
    if not task:
        return None

    key = await get_user_dek(config, user_id)
    normalized = _normalize_steps(steps)
    enc = encrypt(json.dumps(normalized), key) if normalized else None

    now = datetime.now(timezone.utc).isoformat()
    async with db_session(config) as db:
        await db.execute(
            "UPDATE tasks SET steps = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (enc, now, task_id, user_id),
        )
        await db.commit()

    return normalized


async def append_steps(
    config: Config,
    user_id: str,
    task_id: str,
    items: list[str],
) -> list[dict] | None:
    """Append checklist items to a task's steps, skipping duplicate titles.

    Dedup is case-insensitive and whitespace-trimmed against the existing
    titles. Builds a NEW step list (the fetched task is never mutated) and
    persists it through the existing ``set_steps`` encrypt/write path.

    Returns the normalized full step list, or None if the task is missing.
    """
    task = await get_task(config, user_id, task_id)
    if not task:
        return None

    existing = _normalize_steps(_parse_steps(task.get("steps")))
    seen_titles = {(s.get("title") or "").strip().casefold() for s in existing}

    appended: list[dict] = []
    for raw in items or []:
        title = (raw or "").strip() if isinstance(raw, str) else str(raw or "").strip()
        if not title:
            continue
        key = title.casefold()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        appended.append({"title": title, "done": False})

    if not appended:
        # Nothing new to add — return the current normalized list unchanged.
        return existing

    # Immutable: a brand-new list, never an in-place edit of ``existing``.
    merged = [*existing, *appended]
    return await set_steps(config, user_id, task_id, merged)


async def toggle_step(
    config: Config,
    user_id: str,
    task_id: str,
    step_id: str,
) -> dict | None:
    """Flip the `done` flag on a single step. Returns the updated task dict."""
    task = await get_task(config, user_id, task_id)
    if not task:
        return None

    current = _parse_steps(task.get("steps"))
    matched = False
    for step in current:
        if step.get("id") == step_id:
            step["done"] = not bool(step.get("done"))
            matched = True
            break

    if not matched:
        return None

    await set_steps(config, user_id, task_id, current)
    return await get_task(config, user_id, task_id)


# ---------------------------------------------------------------------------
# Progress log — encrypted JSON timeline of {ts, kind, text, source}.
# ---------------------------------------------------------------------------


def _decrypt_progress_log(raw: str | None, key: bytes) -> list[dict]:
    """Decrypt + parse a progress_log column value. Empty/garbage → []."""
    if not raw:
        return []
    try:
        decrypted = decrypt_field(raw, key)
        if not decrypted:
            return []
        parsed = json.loads(decrypted)
    except Exception:
        logger.debug("malformed progress_log; returning empty", exc_info=True)
        return []
    return parsed if isinstance(parsed, list) else []


async def append_progress_entry(
    config: Config,
    user_id: str,
    task_id: str,
    *,
    kind: str,
    text: str | None = None,
    source: str = "manual",
) -> dict | None:
    """Append a single timestamped entry to a task's progress log.

    Returns the appended entry dict, or None if the task doesn't exist.
    Mirrors a ``[!progress]`` callout to the task's LazyBrain note so
    the timeline is searchable in the PKM. Mirror failures are logged
    but never block the write — the canonical record is the encrypted
    log on the task row.
    """
    task = await get_task(config, user_id, task_id)
    if not task:
        return None

    key = await get_user_dek(config, user_id)
    current = _decrypt_progress_log(task.get("progress_log"), key)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": _normalize_progress_kind(kind),
        "text": (text or "").strip()[:500] or None,
        "source": _normalize_progress_source(source),
    }
    current.append(entry)
    # Cap at 200 entries — keeps decrypt cheap and the LazyBrain mirror
    # readable. Older entries fall off; the LazyBrain note keeps a full
    # archived view.
    if len(current) > 200:
        current = current[-200:]

    enc = encrypt(json.dumps(current), key)
    now_iso = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE tasks SET progress_log = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (enc, now_iso, task_id, user_id),
        )
        await db.commit()

    # Mirror a [!progress] callout to the task's LazyBrain note. Heals
    # missing notes via _mirror_status_to_lazybrain on the next status
    # transition; we don't force-create here to avoid PKM noise on every
    # NL phrase.
    try:
        from lazyclaw.lazybrain import store as lb_store
        note_id = task.get("lazybrain_note_id")
        if note_id:
            note = await lb_store.get_note(config, user_id, note_id)
            if note is not None:
                ts_short = entry["ts"][11:16]  # HH:MM
                kind = entry["kind"]
                txt = entry["text"] or ""
                callout = (
                    f"\n\n> [!progress] {ts_short} · {kind}\n> {txt}"
                    if txt else f"\n\n> [!progress] {ts_short} · {kind}"
                )
                new_body = (note.get("content") or "") + callout
                await lb_store.update_note(
                    config, user_id, note_id, content=new_body,
                )
    except Exception:
        logger.debug("progress mirror to LazyBrain failed", exc_info=True)

    return entry


async def read_progress_log(
    config: Config,
    user_id: str,
    task_id: str,
    *,
    limit: int = 20,
) -> list[dict]:
    """Decrypt and return the most recent ``limit`` progress entries.

    Returns oldest-first within the trailing window so callers can
    render the timeline naturally.
    """
    task = await get_task(config, user_id, task_id)
    if not task:
        return []
    key = await get_user_dek(config, user_id)
    log = _decrypt_progress_log(task.get("progress_log"), key)
    if limit and len(log) > limit:
        return log[-limit:]
    return log


async def get_task_changes(
    config: Config,
    user_id: str,
    since: str | None,
) -> dict:
    """Return a delta feed for offline-sync clients.

    Returns tasks where ``updated_at > since`` (including soft-deleted
    tombstones so clients learn of deletes). When ``since`` is None, all
    rows are returned.

    Response shape::

        {
            "tasks":   [<live task dicts>],
            "deleted": [<id, ...>],          # ids of soft-deleted rows
            "now":     "<server iso>",       # use as next `since` value
        }
    """
    key = await get_user_dek(config, user_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    if since is not None:
        async with db_session(config) as db:
            cursor = await db.execute(
                f"SELECT {TASK_SELECT} FROM tasks "
                "WHERE user_id = ? AND updated_at > ? "
                "ORDER BY updated_at ASC",
                (user_id, since),
            )
            rows = await cursor.fetchall()
    else:
        async with db_session(config) as db:
            cursor = await db.execute(
                f"SELECT {TASK_SELECT} FROM tasks "
                "WHERE user_id = ? "
                "ORDER BY updated_at ASC",
                (user_id,),
            )
            rows = await cursor.fetchall()

    live_tasks: list[dict] = []
    deleted_ids: list[str] = []
    deleted_col_idx = TASK_COLUMNS.index("deleted_at")
    id_col_idx = TASK_COLUMNS.index("id")

    for row in rows:
        if row[deleted_col_idx] is not None:
            # Tombstone — client only needs the id to remove from local store.
            deleted_ids.append(row[id_col_idx])
        else:
            live_tasks.append(_row_to_dict(row, key))

    return {
        "tasks": live_tasks,
        "deleted": deleted_ids,
        "now": now_iso,
    }


async def clear_nudge_sent(
    config: Config, user_id: str, task_id: str,
) -> None:
    """Clear ``nudge_sent_at`` so the stale detector can fire again next
    time the task goes silent. Called from any path that registers user
    intent on a task (callback, NL response, manual edit)."""
    async with db_session(config) as db:
        await db.execute(
            "UPDATE tasks SET nudge_sent_at = NULL "
            "WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
        await db.commit()
