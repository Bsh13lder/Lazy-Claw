"""Persistent Team Lead — orchestrates and tracks all agent work.

Not an LLM. A stateful coordinator that gives instant status,
tracks progress across foreground, background, and specialist lanes.
Lives for the session lifetime (in-memory only, resets on restart).
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, replace


# ── Status query detection ────────────────────────────────────────────

_STATUS_RE = re.compile(
    r"^("
    r"/status|/tasks|/\?|\?"
    r"|status"
    r"|what.s happening|whats happening"
    r"|what.s running|whats running"
    r"|what.s going on|whats going on"
    r"|what is happening"
    r"|what are you doing"
    r"|are you working"
    r"|progress"
    r"|how.s it going"
    r"|what happened"
    r"|what did you do"
    r"|any updates"
    r")$",
    re.IGNORECASE,
)

_CANCEL_RE = re.compile(r"^cancel\s+(.+)$", re.IGNORECASE)

_MAX_HISTORY = 20
_STATUS_RECENT_COUNT = 5


# ── Data model ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TrackedTask:
    """Immutable snapshot of a tracked task."""

    task_id: str
    name: str              # Short display name (e.g. "chat", "auto_browser")
    description: str       # First 80 chars of user message / instruction
    lane: str              # "foreground" | "background" | "specialist"
    status: str            # "running" | "done" | "failed" | "cancelled"
    started_at: float      # time.monotonic()
    completed_at: float | None = None
    current_step: str = ""       # Last tool name or phase
    step_count: int = 0          # Number of tool calls so far
    result_preview: str = ""     # First 100 chars of result
    error: str = ""              # Error message if failed


# ── Team Lead ─────────────────────────────────────────────────────────

class TeamLead:
    """Persistent coordinator — lives for the entire session.

    Thread-safe under asyncio (single-threaded event loop).
    All mutations produce new TrackedTask objects (immutable pattern).
    """

    def __init__(self) -> None:
        self._active: dict[str, TrackedTask] = {}
        self._history: deque[TrackedTask] = deque(maxlen=_MAX_HISTORY)

    # ── Registration ──────────────────────────────────────────────

    def register(
        self,
        task_id: str,
        name: str,
        description: str,
        lane: str,
    ) -> TrackedTask:
        """Register a new running task. Returns the created snapshot."""
        task = TrackedTask(
            task_id=task_id,
            name=name,
            description=description[:80],
            lane=lane,
            status="running",
            started_at=time.monotonic(),
        )
        self._active[task_id] = task
        return task

    def update_step(self, task_id: str, step_name: str) -> None:
        """Update current step for a running task (no-op if not found)."""
        task = self._active.get(task_id)
        if task is not None:
            self._active[task_id] = replace(
                task,
                step_count=task.step_count + 1,
                current_step=step_name,
            )

    # ── Lifecycle ─────────────────────────────────────────────────

    def complete(self, task_id: str, result_preview: str = "") -> None:
        """Mark task as done and move to history."""
        task = self._active.pop(task_id, None)
        if task is not None:
            self._history.append(replace(
                task,
                status="done",
                completed_at=time.monotonic(),
                result_preview=result_preview[:100],
                current_step="",
            ))

    def fail(self, task_id: str, error: str = "") -> None:
        """Mark task as failed and move to history."""
        task = self._active.pop(task_id, None)
        if task is not None:
            self._history.append(replace(
                task,
                status="failed",
                completed_at=time.monotonic(),
                error=error[:200],
                current_step="",
            ))

    def cancel(self, task_id: str) -> None:
        """Mark task as cancelled and move to history."""
        task = self._active.pop(task_id, None)
        if task is not None:
            self._history.append(replace(
                task,
                status="cancelled",
                completed_at=time.monotonic(),
                current_step="",
            ))

    @property
    def active_count(self) -> int:
        """Number of currently running tasks."""
        return len(self._active)

    @property
    def active_tasks(self) -> list[TrackedTask]:
        """Snapshot of all active tasks (safe to iterate)."""
        return list(self._active.values())

    @property
    def recent_tasks(self) -> list[TrackedTask]:
        """Last N completed tasks (most recent first)."""
        return list(reversed(self._history))

    # ── Query ─────────────────────────────────────────────────────

    @staticmethod
    def is_status_query(message: str) -> bool:
        """Check if message is a status query (no LLM needed)."""
        return _STATUS_RE.match(message.strip()) is not None

    @staticmethod
    def is_cancel_command(message: str) -> tuple[bool, str]:
        """Check if message is a cancel command. Returns (matched, target)."""
        m = _CANCEL_RE.match(message.strip())
        if m:
            return True, m.group(1)
        return False, ""

    def find_cancel_target(self, target: str) -> str | None:
        """Find active task_id matching target string. Returns task_id or None."""
        lower = target.lower()
        for task_id, task in self._active.items():
            if (
                lower in task.description.lower()
                or lower in task.name.lower()
            ):
                return task_id
        return None

    # ── Formatting ────────────────────────────────────────────────

    def format_status(self) -> str:
        """Instant status — no LLM call, no DB query."""
        now = time.monotonic()
        lines: list[str] = []

        # Active tasks
        if self._active:
            lines.append("Running:")
            for t in self._active.values():
                elapsed = now - t.started_at
                step_info = (
                    f" \u2014 step {t.step_count}: {t.current_step}"
                    if t.current_step
                    else ""
                )
                icon = _lane_icon(t.lane, t.name)
                lines.append(
                    f"  {icon} {t.description[:60]} "
                    f"({t.lane}, {elapsed:.0f}s){step_info}"
                )

        # Recent completed (last N — all history entries are terminal)
        recent = list(reversed(self._history))[:_STATUS_RECENT_COUNT]

        if recent:
            lines.append("\nRecent:")
            for t in recent:
                ago = now - (t.completed_at or now)
                icon = _status_icon(t.status)
                detail = ""
                if t.result_preview:
                    detail = f" \u2014 {t.result_preview[:80]}"
                elif t.error:
                    detail = f" \u2014 {t.error[:80]}"
                lines.append(
                    f"  {icon} {t.description[:60]} ({ago:.0f}s ago){detail}"
                )

        if not self._active and not recent:
            return "All clear \u2014 no tasks running."

        return "\n".join(lines)


def _lane_icon(lane: str, name: str) -> str:
    """Pick icon based on lane and task name."""
    if "browser" in name.lower():
        return "\U0001f310"  # globe
    if lane == "background":
        return "\u26a1"  # lightning
    if lane == "specialist":
        return "\U0001f9d1\u200d\U0001f4bb"  # technologist
    return "\u25cf"  # bullet


def _status_icon(status: str) -> str:
    """Pick icon for completed task status."""
    if status == "done":
        return "\u2713"
    if status == "failed":
        return "\u2717"
    return "\u2014"  # cancelled
