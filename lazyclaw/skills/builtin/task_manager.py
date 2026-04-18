"""Task manager skills — add, list, complete, update, delete tasks + daily briefing.

Personal second-brain system with encrypted storage, AI categorization (via
mcp-taskai), nagging reminders, and Things 3-style buckets (Today/Upcoming/Someday).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Relative time pattern: +10m, +1h, +2h30m, +1d, +1d2h, etc.
_RELATIVE_RE = re.compile(
    r"^\+?\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?$",
    re.IGNORECASE,
)


def _parse_relative_time(value: str) -> datetime | None:
    """Parse relative time strings like '+10m', '+1h', '+2h30m', '+1d'.

    Returns absolute UTC datetime, or None if not a relative format.
    """
    value = value.strip()
    match = _RELATIVE_RE.match(value)
    if not match:
        return None

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)

    if days == 0 and hours == 0 and minutes == 0:
        return None

    return datetime.now(timezone.utc) + timedelta(
        days=days, hours=hours, minutes=minutes
    )

# Priority display
_PRIORITY_ICON = {"urgent": "!!", "high": "!", "medium": "-", "low": "."}
_STATUS_ICON = {"todo": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}


def _get_local_tz() -> timezone:
    """Get Madrid timezone offset (CET/CEST).

    Uses system local time as the offset since the server runs in Madrid.
    Falls back to UTC+1 (CET) if detection fails.
    """
    try:
        import time as _time
        # Use system's local UTC offset (accounts for DST automatically)
        offset_s = -_time.timezone if _time.daylight == 0 else -_time.altzone
        return timezone(timedelta(seconds=offset_s))
    except Exception:
        logger.debug("Failed to detect local timezone, falling back to CET", exc_info=True)
        return timezone(timedelta(hours=1))  # CET fallback


def _to_local(dt: datetime) -> datetime:
    """Convert a UTC datetime to local (Madrid) time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_get_local_tz())


def _format_time(iso_str: str) -> str:
    """Format an ISO datetime as relative + local absolute time."""
    if not iso_str or len(iso_str) < 10:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        local_dt = _to_local(dt)
        local_time = local_dt.strftime("%H:%M")
        diff = dt - now

        if diff.total_seconds() < 0:
            ago = abs(diff.total_seconds())
            if ago < 3600:
                return f"{int(ago // 60)}m ago ({local_time})"
            return f"{int(ago // 3600)}h ago ({local_time})"
        elif diff.total_seconds() < 3600:
            return f"in {int(diff.total_seconds() // 60)}m ({local_time})"
        elif diff.total_seconds() < 86400:
            h = int(diff.total_seconds() // 3600)
            m = int((diff.total_seconds() % 3600) // 60)
            return f"in {h}h {m}m ({local_time})"
        else:
            return f"in {diff.days}d ({local_dt.strftime('%b %d %H:%M')})"
    except (ValueError, TypeError):
        return iso_str[:16]


def _fuzzy_match_task(tasks: list[dict], name: str) -> dict | None:
    """Find a task by fuzzy name matching."""
    name_lower = name.lower().strip()
    # Exact match first
    for t in tasks:
        if (t.get("title") or "").lower() == name_lower:
            return t
    # Contains match
    for t in tasks:
        title = (t.get("title") or "").lower()
        if name_lower in title or title in name_lower:
            return t
    return None


def _format_task(t: dict, show_status: bool = True, show_owner: bool = False) -> str:
    """Format a single task as a readable line."""
    icon = _STATUS_ICON.get(t.get("status", "todo"), "[ ]")
    pri = _PRIORITY_ICON.get(t.get("priority", "medium"), "-")
    title = t.get("title", "?")
    due = t.get("due_date", "")
    cat = t.get("category", "")
    owner = t.get("owner", "user")

    parts = []
    if show_status:
        parts.append(icon)
    if show_owner:
        parts.append("[AI]" if owner == "agent" else "[ME]")
    parts.append(f"{pri} {title}")
    reminder = t.get("reminder_at", "")
    if reminder:
        parts.append(f"({_format_time(reminder)})")
    elif due:
        parts.append(f"(due {due})")
    if cat:
        parts.append(f"[{cat}]")
    if t.get("recurring"):
        parts.append("[recurring]")
    return " ".join(parts)


class AddTaskSkill(BaseSkill):
    """Add a new task with optional AI categorization."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "add_task"

    @property
    def description(self) -> str:
        return (
            "Add a task with optional reminder. PREFERRED over set_reminder for ALL "
            "reminder requests — supports relative times (+10m, +1h) calculated server-side. "
            "Two types: owner='user' (human tasks), owner='agent' (AI tasks). "
            "Examples: 'remind me call dentist' → add_task with reminder_at='+10m'. "
            "'after 30 minutes drink water' → add_task with reminder_at='+30m'. "
            "'research flights' → owner=agent. "
            "ALWAYS use relative reminder_at (+Xm/+Xh) instead of calculating ISO times."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Task title (e.g. 'Send the order')",
                },
                "description": {
                    "type": "string",
                    "description": "Optional longer description",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "urgent"],
                    "description": "Task priority (default: medium)",
                },
                "due_date": {
                    "type": "string",
                    "description": "Due date in ISO format YYYY-MM-DD (e.g. '2026-03-30')",
                },
                "reminder_at": {
                    "type": "string",
                    "description": (
                        "When to send a reminder. Accepts RELATIVE times: "
                        "'+10m' (10 minutes), '+1h' (1 hour), '+2h30m' (2.5 hours), "
                        "'+1d' (1 day). ALWAYS use relative format for 'in X minutes/hours'. "
                        "Also accepts ISO datetime (e.g. '2026-03-30T21:00:00') for specific times. "
                        "Fires via Telegram with Done/Snooze/Tomorrow buttons."
                    ),
                },
                "recurring": {
                    "type": "string",
                    "description": (
                        "Cron expression for recurring tasks "
                        "(e.g. '0 9 * * 1' for every Monday at 9am). "
                        "When completed, the next occurrence auto-creates."
                    ),
                },
                "owner": {
                    "type": "string",
                    "enum": ["user", "agent"],
                    "description": (
                        "REQUIRED BEHAVIOR — pay attention: "
                        "When the HUMAN asks you to create a task (in chat, via any "
                        "channel), you MUST pass owner='user'. This is their task. "
                        "ONLY use owner='agent' for tasks YOU decide to create for "
                        "yourself (background research, self-scheduled checks, work "
                        "the user did not explicitly ask for). "
                        "Phrases like 'remind me', 'add a task for me', 'I need to', "
                        "'don't let me forget' → owner='user' every time. "
                        "Default when unsure: 'user'."
                    ),
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags (e.g. ['work', 'urgent'])",
                },
            },
            "required": ["title"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.store import create_task, list_tasks

        title = params.get("title", "").strip()
        if not title:
            return "Task title is required."

        # Parse reminder_at — supports relative (+10m, +1h, +2h30m, +1d) and ISO
        reminder_at = params.get("reminder_at")
        if reminder_at:
            parsed_dt = _parse_relative_time(reminder_at)
            if parsed_dt:
                reminder_at = parsed_dt.isoformat()
            else:
                try:
                    dt = datetime.fromisoformat(reminder_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt <= datetime.now(timezone.utc):
                        return f"Reminder time '{reminder_at}' is in the past."
                    reminder_at = dt.isoformat()
                except ValueError:
                    return f"Invalid reminder time: '{reminder_at}'. Use '+10m', '+1h', or ISO datetime."

        # Validate recurring cron if provided
        recurring = params.get("recurring")
        if recurring:
            from lazyclaw.heartbeat.cron import is_valid
            if not is_valid(recurring):
                return f"Invalid cron expression: '{recurring}'"

        try:
            task = await create_task(
                self._config, user_id,
                title=title,
                description=params.get("description"),
                category=None,  # AI will set this
                priority=params.get("priority", "medium"),
                owner=params.get("owner", "user"),
                due_date=params.get("due_date"),
                reminder_at=reminder_at,
                recurring=recurring,
                tags=params.get("tags"),
            )
        except Exception as exc:
            logger.error("Failed to create task: %s", exc, exc_info=True)
            return f"Failed to create task: {exc}"

        result_parts = [f"Task added: {title}"]
        if task.get("due_date"):
            result_parts.append(f"Due: {task['due_date']}")
        if task.get("reminder_at"):
            result_parts.append(f"Reminder: {task['reminder_at']}")
        if task.get("recurring"):
            result_parts.append(f"Recurring: {task['recurring']}")

        # Fire-and-forget: AI categorize + duplicate check
        ai_notes = await _smart_enrich(
            self._config, user_id, task["id"], title
        )
        if ai_notes:
            result_parts.append(ai_notes)

        return "\n".join(result_parts)


class ListTasksSkill(BaseSkill):
    """List tasks with bucket/status/priority filters."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_tasks"

    @property
    def description(self) -> str:
        return (
            "List tasks. Two lists: owner='user' (human tasks), owner='agent' (AI tasks). "
            "Buckets: 'today', 'upcoming', 'someday'. "
            "Default: shows all active user tasks. Use owner='agent' for AI's todo list."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "enum": ["user", "agent", "all"],
                    "description": "Whose tasks? 'user' (human), 'agent' (AI), 'all' (both). Default: user.",
                },
                "bucket": {
                    "type": "string",
                    "enum": ["today", "upcoming", "someday", "all"],
                    "description": "Time bucket (default: all active)",
                },
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "done", "all"],
                    "description": "Filter by status (default: active = todo + in_progress)",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "urgent"],
                    "description": "Filter by priority",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.store import list_tasks

        bucket = params.get("bucket")
        status = params.get("status")
        priority = params.get("priority")
        owner = params.get("owner", "user")
        if owner == "all":
            owner = None

        # Default: show active tasks (not done/cancelled)
        if not status and not bucket:
            status = "todo"

        try:
            tasks = await list_tasks(
                self._config, user_id,
                status=status, priority=priority, bucket=bucket,
                owner=owner,
            )
        except Exception as exc:
            return f"Failed to list tasks: {exc}"

        # Also include pending reminders from agent_jobs
        reminders = await _get_pending_reminders(self._config, user_id)

        if not tasks and not reminders:
            return "No tasks or reminders found. DONE — no more tool calls needed."

        lines = []
        if reminders:
            lines.append(f"Reminders ({len(reminders)}):")
            for r in reminders:
                lines.append(f"  - {r['name']}  ({_format_time(r['next_run'])})")
            lines.append("")

        if tasks:
            lines.append(f"Tasks ({len(tasks)}):")
            for t in tasks:
                lines.append(f"  {_format_task(t)}")

        return "\n".join(lines)


class CompleteTaskSkill(BaseSkill):
    """Mark a task as done by name."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def description(self) -> str:
        return (
            "Mark a task as completed. Matches by name (partial match works). "
            "Recurring tasks auto-create the next occurrence."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_name": {
                    "type": "string",
                    "description": "Task name or partial name to match",
                },
            },
            "required": ["task_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.store import complete_task, list_tasks

        task_name = params.get("task_name", "").strip()
        if not task_name:
            return "Task name is required."

        tasks = await list_tasks(self._config, user_id, status="todo")
        tasks += await list_tasks(self._config, user_id, status="in_progress")

        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            available = ", ".join(t.get("title", "?") for t in tasks[:5])
            return f"No task matching '{task_name}'. Active tasks: {available}"

        ok = await complete_task(self._config, user_id, match["id"])
        if ok:
            msg = f"Completed: {match['title']}"
            if match.get("recurring"):
                msg += " (next occurrence created)"
            return msg
        return "Failed to complete task."


class UpdateTaskSkill(BaseSkill):
    """Update task fields by name."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "update_task"

    @property
    def description(self) -> str:
        return (
            "Update a task's title, priority, due date, reminder, or status. "
            "Matches by name (partial match)."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_name": {
                    "type": "string",
                    "description": "Task name or partial name to match",
                },
                "title": {"type": "string", "description": "New title"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "urgent"],
                },
                "due_date": {"type": "string", "description": "New due date (YYYY-MM-DD)"},
                "reminder_at": {"type": "string", "description": "New reminder (ISO datetime)"},
                "status": {
                    "type": "string",
                    "enum": ["todo", "in_progress", "done", "cancelled"],
                },
                "description": {"type": "string"},
            },
            "required": ["task_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.store import list_tasks, update_task

        task_name = params.pop("task_name", "").strip()
        if not task_name:
            return "Task name is required."

        tasks = await list_tasks(self._config, user_id)
        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            return f"No task matching '{task_name}'."

        # Filter to only provided fields
        updates = {k: v for k, v in params.items() if v is not None}
        if not updates:
            return "No fields to update."

        ok = await update_task(self._config, user_id, match["id"], **updates)
        if ok:
            return f"Updated: {match['title']}"
        return "Failed to update task."


class DeleteTaskSkill(BaseSkill):
    """Delete a task by name."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "delete_task"

    @property
    def description(self) -> str:
        return "Delete a task permanently. Matches by name (partial match)."

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_name": {
                    "type": "string",
                    "description": "Task name or partial name to match",
                },
            },
            "required": ["task_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.store import delete_task, list_tasks

        task_name = params.get("task_name", "").strip()
        if not task_name:
            return "Task name is required."

        tasks = await list_tasks(self._config, user_id)
        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            return f"No task matching '{task_name}'."

        ok = await delete_task(self._config, user_id, match["id"])
        return f"Deleted: {match['title']}" if ok else "Failed to delete task."


class DailyBriefingSkill(BaseSkill):
    """Show today's tasks, overdue items, and upcoming schedule."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "daily_briefing"

    @property
    def description(self) -> str:
        return (
            "Show a daily briefing: overdue tasks, today's tasks, and upcoming "
            "(next 3 days). Use when the user asks 'what do I have today?', "
            "'my tasks', 'daily briefing', 'what's on my plate?'."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.store import list_tasks

        today_str = date.today().isoformat()
        upcoming_str = (date.today() + timedelta(days=3)).isoformat()

        try:
            all_active = await list_tasks(self._config, user_id, status="todo")
            all_active += await list_tasks(self._config, user_id, status="in_progress")
        except Exception as exc:
            return f"Failed to load tasks: {exc}"

        # Also fetch pending reminders from agent_jobs (set_reminder creates these)
        reminders = await _get_pending_reminders(self._config, user_id)

        if not all_active and not reminders:
            return (
                "No active tasks or reminders. Your plate is clean!\n"
                "DONE — show this result to the user. Do NOT call any other tools."
            )

        overdue = []
        today_tasks = []
        upcoming = []
        someday = []

        for t in all_active:
            due = t.get("due_date")
            reminder = t.get("reminder_at", "")
            # Tasks with reminder today but no due_date → treat as today
            if not due and reminder and reminder[:10] <= today_str:
                today_tasks.append(t)
            elif not due:
                someday.append(t)
            elif due < today_str:
                overdue.append(t)
            elif due == today_str:
                today_tasks.append(t)
            elif due <= upcoming_str:
                upcoming.append(t)
            else:
                upcoming.append(t)

        lines = [f"Daily Briefing ({date.today().strftime('%A, %b %d')})"]
        lines.append("")

        if reminders:
            lines.append(f"REMINDERS ({len(reminders)}):")
            for r in reminders:
                lines.append(f"  - {r['name']}  ({_format_time(r['next_run'])})")
            lines.append("")

        if overdue:
            lines.append(f"OVERDUE ({len(overdue)}):")
            for t in overdue:
                lines.append(f"  {_format_task(t, show_status=False)}")
            lines.append("")

        if today_tasks:
            lines.append(f"TODAY ({len(today_tasks)}):")
            for t in today_tasks:
                lines.append(f"  {_format_task(t, show_status=False)}")
            lines.append("")

        if upcoming:
            lines.append(f"UPCOMING ({len(upcoming)}):")
            for t in upcoming:
                lines.append(f"  {_format_task(t, show_status=False)}")
            lines.append("")

        if someday:
            lines.append(f"SOMEDAY ({len(someday)}):")
            for t in someday[:5]:
                lines.append(f"  {_format_task(t, show_status=False)}")
            if len(someday) > 5:
                lines.append(f"  ... and {len(someday) - 5} more")

        lines.append("\nDONE — show this to the user. No more tool calls needed.")
        return "\n".join(lines)


class StopBackgroundSkill(BaseSkill):
    """Stop running background tasks."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "stop_background"

    @property
    def description(self) -> str:
        return (
            "Stop/cancel running background tasks. Use when user says "
            "'stop tasks', 'cancel tasks', 'stop all', 'stop background'. "
            "Optionally specify a task name to cancel just one."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_name": {
                    "type": "string",
                    "description": "Name or partial name to cancel (omit to cancel all)",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        # Access task_runner via the agent's reference
        try:
            from lazyclaw.runtime.task_runner import _task_runner_instance
            runner = _task_runner_instance
        except (ImportError, AttributeError):
            runner = None

        if not runner:
            # Fallback: try to find it via config
            return (
                "Cannot access task runner directly. "
                "Use /cancel in Telegram to stop background tasks."
            )

        task_name = params.get("task_name", "")
        running = runner.list_running(user_id)

        if not running:
            return "No background tasks running."

        cancelled = 0
        for t in running:
            tid = t.get("id", "")
            name = t.get("name", "")
            if task_name and task_name.lower() not in name.lower():
                continue
            ok = await runner.cancel(tid, user_id)
            if ok:
                cancelled += 1

        if cancelled:
            return f"Cancelled {cancelled} background task(s)."
        if task_name:
            names = ", ".join(t.get("name", "?") for t in running)
            return f"No task matching '{task_name}'. Running: {names}"
        return "Failed to cancel tasks."


class WorkTodosSkill(BaseSkill):
    """AI executes its own todo list — prioritizes and runs tasks autonomously."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "work_todos"

    @property
    def description(self) -> str:
        return (
            "Execute the AI's todo list. Reads all agent-owned tasks, prioritizes them, "
            "and executes them one by one (or parallel via run_background). "
            "Use when user says 'do your todos', 'work on your tasks', "
            "'do the todo list', 'execute your jobs'. "
            "Each completed task is marked done. Reports progress."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "max_tasks": {
                    "type": "integer",
                    "description": "Max tasks to execute this run (default: 5)",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.store import list_tasks

        max_tasks = params.get("max_tasks", 5)

        try:
            agent_tasks = await list_tasks(
                self._config, user_id, status="todo", owner="agent",
            )
            in_progress = await list_tasks(
                self._config, user_id, status="in_progress", owner="agent",
            )
            agent_tasks = in_progress + agent_tasks
        except Exception as exc:
            return f"Failed to load agent tasks: {exc}"

        if not agent_tasks:
            return "No AI tasks in the queue. Add tasks with owner='agent'."

        # Truncate to max
        batch = agent_tasks[:max_tasks]

        # Build execution plan for the LLM to follow
        lines = [
            f"AI TODO LIST — {len(batch)} task(s) to execute "
            f"(of {len(agent_tasks)} total):",
            "",
        ]
        for i, t in enumerate(batch, 1):
            pri = _PRIORITY_ICON.get(t.get("priority", "medium"), "-")
            desc = t.get("description") or ""
            lines.append(f"{i}. {pri} {t['title']}")
            if desc:
                lines.append(f"   Details: {desc}")
            lines.append(f"   ID: {t['id']}")
            lines.append("")

        lines.append(
            "INSTRUCTIONS: Execute these tasks NOW using your available tools. "
            "For each task: do the work, then call complete_task(task_name='...'). "
            "Use run_background for tasks that can run in parallel. "
            "If a task needs info you don't have, skip it and explain why."
        )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers — fetch reminders from agent_jobs (for briefing)
# ---------------------------------------------------------------------------

async def _get_pending_reminders(config, user_id: str) -> list[dict]:
    """Fetch active reminders from agent_jobs (created by set_reminder skill)."""
    try:
        from lazyclaw.heartbeat.orchestrator import list_jobs
        jobs = await list_jobs(config, user_id)
        return [
            {"name": j.get("name", "?"), "next_run": j.get("next_run", "?")}
            for j in jobs
            if j.get("job_type") == "reminder" and j.get("status") == "active"
        ]
    except Exception:
        logger.debug("Failed to fetch pending reminders from agent_jobs", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# AI enrichment (fire-and-forget, never blocks task creation)
# ---------------------------------------------------------------------------

async def _smart_enrich(
    config, user_id: str, task_id: str, title: str
) -> str:
    """Try to categorize task via mcp-taskai. Returns note string or empty."""
    try:
        from mcp_taskai.config import load_config as load_ai_config
        from mcp_taskai.ai_client import AIClient
        from mcp_taskai.intelligence import TaskIntelligence
    except ImportError:
        return ""

    try:
        ai_config = load_ai_config()
        intelligence = TaskIntelligence(AIClient(ai_config))

        result = await intelligence.categorize(title)
        category = result.get("category")

        if category:
            from lazyclaw.tasks.store import update_task
            await update_task(config, user_id, task_id, category=category)
            return f"AI: categorized as '{category}'"
    except Exception:
        logger.debug("AI enrichment failed for task %s", task_id, exc_info=True)

    return ""
