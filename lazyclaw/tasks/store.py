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

ENCRYPTED_FIELDS = frozenset({"title", "description", "category", "tags"})

TASK_COLUMNS = [
    "id", "user_id", "title", "description", "category", "priority",
    "status", "owner", "due_date", "reminder_at", "reminder_job_id", "recurring",
    "tags", "nag_count", "created_at", "completed_at",
]

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
) -> dict:
    """Create a new task. Returns the full task dict (decrypted)."""
    key = await get_user_dek(config, user_id)
    task_id = str(uuid4())

    enc_title = encrypt(title, key)
    enc_description = _encrypt_field(description, key)
    enc_category = _encrypt_field(category, key)
    enc_tags = _encrypt_field(json.dumps(tags), key) if tags else None

    # Create a reminder job if reminder_at is set
    reminder_job_id = None
    if reminder_at:
        reminder_job_id = await _create_reminder_job(
            config, user_id, title, reminder_at, task_id
        )
        # Auto-set due_date from reminder_at if not provided
        if not due_date:
            due_date = reminder_at[:10]

    async with db_session(config) as db:
        await db.execute(
            f"INSERT INTO tasks ({TASK_SELECT}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, user_id, enc_title, enc_description, enc_category,
                priority, "todo", owner, due_date, reminder_at, reminder_job_id,
                recurring, enc_tags, 0,
                datetime.now(timezone.utc).isoformat(), None,
            ),
        )
        await db.commit()

    logger.debug("Created task %s (%s) for user %s", task_id, owner, user_id)

    # Mirror into LazyBrain so the user's second brain also remembers this.
    # Fire-and-forget; task creation must not fail if the PKM is unreachable.
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
        lb_events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"], source="task",
        )
    except Exception:
        logger.debug("lazybrain task mirror failed", exc_info=True)

    return {
        "id": task_id, "user_id": user_id, "title": title,
        "description": description, "category": category,
        "priority": priority, "status": "todo", "owner": owner,
        "due_date": due_date,
        "reminder_at": reminder_at, "reminder_job_id": reminder_job_id,
        "recurring": recurring, "tags": json.dumps(tags) if tags else None,
        "nag_count": 0, "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
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

    where_clauses = ["user_id = ?"]
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
            f"SELECT {TASK_SELECT} FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
        row = await cursor.fetchone()

    return _row_to_dict(row, key) if row else None


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
            "AND reminder_at IS NOT NULL AND reminder_at <= ? "
            "AND nag_count > 0"
        )
    else:
        params = (now_iso,)
        where = (
            "WHERE status IN ('todo', 'in_progress') "
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

    for field_name, value in fields.items():
        if field_name in ENCRYPTED_FIELDS and value is not None:
            value = encrypt(value, key)
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

    # Auto-set completed_at when status becomes done
    if fields.get("status") == "done" and "completed_at" not in fields:
        set_clauses.append("completed_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())

    params.extend([task_id, user_id])

    async with db_session(config) as db:
        result = await db.execute(
            f"UPDATE tasks SET {', '.join(set_clauses)} "
            "WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()
        return result.rowcount > 0


async def complete_task(
    config: Config, user_id: str, task_id: str
) -> bool:
    """Mark task done. Deletes reminder job. Handles recurring (creates next)."""
    task = await get_task(config, user_id, task_id)
    if not task:
        return False

    # Delete reminder job if exists
    if task.get("reminder_job_id"):
        await _delete_reminder_job(config, user_id, task["reminder_job_id"])

    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE tasks SET status = 'done', completed_at = ?, "
            "reminder_job_id = NULL, nag_count = 0 "
            "WHERE id = ? AND user_id = ?",
            (now, task_id, user_id),
        )
        await db.commit()

    # Recurring: create the next occurrence
    if task.get("recurring"):
        try:
            from lazyclaw.heartbeat.cron import get_next_run
            next_due = get_next_run(task["recurring"])
            next_date = next_due[:10] if next_due else None
            # Calculate reminder_at offset from original due_date
            next_reminder = None
            if task.get("reminder_at") and task.get("due_date"):
                try:
                    orig_due = datetime.fromisoformat(task["due_date"])
                    orig_rem = datetime.fromisoformat(task["reminder_at"])
                    offset = orig_rem - orig_due
                    next_due_dt = datetime.fromisoformat(next_due)
                    next_reminder = (next_due_dt + offset).isoformat()
                except (ValueError, TypeError):
                    logger.debug("Failed to compute next reminder offset, using next_due", exc_info=True)
                    next_reminder = next_due

            tags = None
            if task.get("tags"):
                try:
                    tags = json.loads(task["tags"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Could not parse tags JSON for task %r; skipping tags", task.get("title"))

            await create_task(
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
            logger.debug("Created next recurring task from %s", task_id)
        except Exception:
            logger.warning("Failed to create recurring task", exc_info=True)

    return True


async def delete_task(
    config: Config, user_id: str, task_id: str
) -> bool:
    """Delete a task and its associated reminder job."""
    task = await get_task(config, user_id, task_id)
    if not task:
        return False

    if task.get("reminder_job_id"):
        await _delete_reminder_job(config, user_id, task["reminder_job_id"])

    async with db_session(config) as db:
        result = await db.execute(
            "DELETE FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        )
        await db.commit()
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
