"""NL reschedule + ask-about-task skills.

Why these are separate from ``UpdateTaskSkill``:

``UpdateTaskSkill`` takes discrete fields (``due_date``, ``reminder_at``,
``priority``, ``status``). It's the right tool when the brain already
parsed the user's intent into structured params. But every NL phrase the
user actually types — "snooze 2h", "push to Friday 10", "what's on my
plate?", "move to next Monday", "clear the deadline" — has to be
flattened to those discrete fields by the brain LLM, which means an
extra round-trip and a chance to get the field name wrong.

These two skills accept the raw NL phrase and do the parsing locally:

- ``reschedule_task``: phrase + fuzzy task name → resolves new
  ``reminder_at`` / ``priority`` / ``due_date`` and calls ``update_task``.
- ``ask_about_task``: fuzzy task name → returns a friendly summary
  (title, due, recurring, status, last nags, brain note link).

The fuzzy matcher is reused from ``task_manager`` so duplicate-title
disambiguation stays consistent across skills.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# ── NL parsers ─────────────────────────────────────────────────────────

_SNOOZE_RE = re.compile(
    r"\b(?:snooze|delay|defer|aplaza(?:r)?|posponer|nag\s+later)\s+"
    r"(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|"
    r"minutos?|horas?|días?|dias?)\b",
    re.IGNORECASE,
)

_PRIORITY_PHRASE_RE = re.compile(
    r"\b(?:set\s+)?priority\s+(urgent|high|medium|low|urgente|alta|media|baja)\b",
    re.IGNORECASE,
)

_CLEAR_DEADLINE_RE = re.compile(
    r"\b(?:clear|remove|wipe|elimina(?:r)?|borra(?:r)?)\s+"
    # English ("the"), Spanish ("el", "la"), or no article — all valid.
    r"(?:(?:the|el|la)\s+)?"
    r"(?:deadline|due\s+date|reminder|recordatorio|fecha)\b",
    re.IGNORECASE,
)

_PRIORITY_MAP = {
    "urgent": "urgent", "urgente": "urgent",
    "high": "high", "alta": "high",
    "medium": "medium", "media": "medium",
    "low": "low", "baja": "low",
}


def _parse_snooze(phrase: str) -> datetime | None:
    """Parse "snooze 2h" / "delay 30m" / "aplaza 1 día"."""
    m = _SNOOZE_RE.search(phrase)
    if not m:
        return None
    qty = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith(("m", "minu")):
        delta = timedelta(minutes=qty)
    elif unit.startswith(("h", "hr", "hora")):
        delta = timedelta(hours=qty)
    elif unit.startswith(("d", "día", "dia")):
        delta = timedelta(days=qty)
    else:
        return None
    return datetime.now(timezone.utc) + delta


# ── Skill: reschedule_task ─────────────────────────────────────────────


class RescheduleTaskSkill(BaseSkill):
    """Reschedule a task by name + free-form NL phrase.

    One skill that absorbs the dozen variants the user actually types,
    instead of forcing the brain to pick the right discrete field.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "reschedule_task"

    @property
    def description(self) -> str:
        return (
            "Reschedule, snooze, or re-prioritize an existing task by name + "
            "natural-language phrase. Examples: 'snooze 2h', 'push to Friday 10', "
            "'move to next Monday 9am', 'clear the deadline', 'set priority high', "
            "'aplaza 30 minutos'. Resolves the target by fuzzy name match."
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
                    "description": "Task name or partial name to match (fuzzy).",
                },
                "phrase": {
                    "type": "string",
                    "description": (
                        "Free-form NL: 'snooze 2h', 'push to Friday 10am', "
                        "'next Monday 9', 'clear deadline', 'set priority high'."
                    ),
                },
            },
            "required": ["task_name", "phrase"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.skills.builtin.task_manager import _fuzzy_match_task
        from lazyclaw.tasks.nl_time import parse as nl_time_parse
        from lazyclaw.tasks.store import list_tasks, update_task
        from lazyclaw.tasks.timezone import get_user_tz

        task_name = (params.get("task_name") or "").strip()
        phrase = (params.get("phrase") or "").strip()
        if not task_name:
            return "Task name is required."
        if not phrase:
            return "Phrase is required (e.g. 'snooze 2h')."

        tasks = await list_tasks(self._config, user_id)
        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            return f"No task matching '{task_name}'."

        updates: dict = {}
        applied: list[str] = []

        # Priority phrase first — composable with a time phrase in the same
        # input (e.g. "snooze 2h and set priority high").
        pri_match = _PRIORITY_PHRASE_RE.search(phrase)
        if pri_match:
            pri = _PRIORITY_MAP.get(pri_match.group(1).lower())
            if pri:
                updates["priority"] = pri
                applied.append(f"priority → {pri}")

        # Clear-deadline phrase — composable with priority but mutually
        # exclusive with snooze/reschedule on the same input.
        if _CLEAR_DEADLINE_RE.search(phrase):
            updates["reminder_at"] = None
            updates["due_date"] = None
            applied.append("deadline cleared")
        else:
            # Both branches confirm a time back to the user — resolve their
            # zone once so the shown wall-clock matches when it actually
            # fires ("snoozed → Wed 14:00" used to show the UTC clock, 2h
            # off the real Madrid fire time).
            user_tz = await get_user_tz(self._config, user_id)
            # Snooze (relative offset from now)
            snooze_dt = _parse_snooze(phrase)
            if snooze_dt is not None:
                updates["reminder_at"] = snooze_dt.isoformat()
                local_snooze = snooze_dt.astimezone(user_tz)
                updates["due_date"] = local_snooze.date().isoformat()
                applied.append(f"snoozed → {local_snooze.strftime('%a %H:%M')}")
            else:
                # Fall through to the NL time parser ("Friday 10am",
                # "tomorrow 9", "next Monday"). Pass user tz so wall
                # clocks land correctly for non-UTC users.
                parsed = nl_time_parse(phrase, tz=user_tz)
                if parsed.reminder_at:
                    updates["reminder_at"] = parsed.reminder_at
                    if parsed.due_date:
                        updates["due_date"] = parsed.due_date
                    shown = parsed.reminder_at
                    try:
                        shown = (
                            datetime.fromisoformat(parsed.reminder_at)
                            .astimezone(user_tz)
                            .strftime("%a %d %b · %H:%M")
                        )
                    except (ValueError, TypeError):
                        pass
                    applied.append(f"reminder → {shown}")

        if not updates:
            return (
                f"Couldn't extract a reschedule action from '{phrase}'. "
                "Try: 'snooze 2h', 'push to Friday 10am', 'clear deadline', "
                "'set priority high'."
            )

        try:
            ok = await update_task(self._config, user_id, match["id"], **updates)
        except ValueError as exc:
            return f"Reschedule rejected: {exc}"
        if not ok:
            return f"Failed to reschedule '{match['title']}'."

        return f"✏️ {match['title']} — {', '.join(applied)}"


# ── Skill: ask_about_task ──────────────────────────────────────────────


class AskAboutTaskSkill(BaseSkill):
    """Read-only: friendly summary of a task by fuzzy name."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "ask_about_task"

    @property
    def description(self) -> str:
        return (
            "Look up a task by name and return its title, due date, "
            "recurring rule, status, last nag count, and linked LazyBrain "
            "note. Read-only — never changes the task. Use when the user "
            "asks 'what's the deadline on X?' or 'when is Y due?'."
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
                    "description": "Task name or partial name to match (fuzzy).",
                },
            },
            "required": ["task_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.skills.builtin.task_manager import _fuzzy_match_task
        from lazyclaw.tasks.store import list_tasks
        from lazyclaw.tasks.timezone import get_user_tz, parse_user_dt

        task_name = (params.get("task_name") or "").strip()
        if not task_name:
            return "Task name is required."

        tasks = await list_tasks(self._config, user_id)
        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            return f"No task matching '{task_name}'."

        user_tz = await get_user_tz(self._config, user_id)
        lines: list[str] = [f"**{match['title']}**"]

        # Priority + status badge
        pri = match.get("priority") or "medium"
        status = match.get("status") or "todo"
        lines.append(f"_{status} · priority {pri}_")

        if match.get("category"):
            lines.append(f"📁 {match['category']}")

        if match.get("due_date"):
            lines.append(f"📅 Due: {match['due_date']}")

        if match.get("reminder_at"):
            # parse_user_dt reads a naive stored value as the USER's
            # wall-clock (the write-side convention) — reading it as UTC
            # displayed legacy pre-2026-07-25 rows 2h late.
            rem_dt = parse_user_dt(match["reminder_at"], user_tz)
            if rem_dt is not None:
                local = rem_dt.astimezone(user_tz)
                lines.append(f"⏰ Reminder: {local.strftime('%a %d %b · %H:%M')}")
            else:
                lines.append(f"⏰ Reminder: {match['reminder_at']}")

        if match.get("recurring"):
            lines.append(f"🔁 Recurring: {match['recurring']}")

        nag = int(match.get("nag_count") or 0)
        if nag > 0:
            lines.append(f"🔊 Nagged {nag}× so far")

        if match.get("last_error"):
            lines.append(f"❌ Last error: {match['last_error']}")

        note_id = match.get("lazybrain_note_id")
        if note_id:
            lines.append(f"🧠 Brain note: {note_id}")

        # Surface the latest progress entries so the brain — when it
        # picks this skill in response to "how's X going?" — has the
        # actual answer in-line. Read-only; cheap.
        try:
            from lazyclaw.tasks.store import read_progress_log
            log = await read_progress_log(
                self._config, user_id, match["id"], limit=5,
            )
        except Exception:
            log = []
        if log:
            lines.append("")
            lines.append("**Recent progress:**")
            icon_map = {
                "started": "🚀", "working": "🟡", "progress": "📊",
                "stuck": "🔴", "blocked": "🚧",
                "paused": "⏸️", "resumed": "▶️", "done": "✅",
                "pulse_fired": "🔔",
            }
            for entry in log:
                ts_short = (entry.get("ts") or "")[11:16]
                kind = entry.get("kind") or "progress"
                icon = icon_map.get(kind, "📝")
                text = entry.get("text") or ""
                line = f"{icon} {ts_short} · {kind}"
                if text:
                    line += f": {text[:60]}"
                lines.append(line)

        return "\n".join(lines)
