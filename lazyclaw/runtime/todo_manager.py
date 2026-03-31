"""TodoWrite task tracking — TodoManager and Todo dataclass.

Provides real-time task tracking for agent workflows. The agent calls
TodoWrite at the start of complex tasks (3+ steps) and updates status
as it completes each step. Only ONE todo can be in_progress at a time.

Used by: TodoWriteSkill (agent tool), TodoWidget (TUI display).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

TodoStatus = str  # "pending" | "in_progress" | "completed"
_VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})


@dataclass
class Todo:
    id: str
    content: str        # imperative: "Run tests"
    active_form: str    # continuous: "Running tests"
    status: TodoStatus  # pending | in_progress | completed
    created_at: str
    updated_at: str


class TodoManager:
    """Session-scoped todo list with JSON persistence and change listeners.

    Only one todo can be in_progress at a time. Setting a second todo to
    in_progress automatically demotes the previous one back to pending.
    """

    def __init__(self, data_dir: Path, user_id: str) -> None:
        self._path = data_dir / f"todos_{user_id}.json"
        self._todos: list[Todo] = []
        self._listeners: list[Callable[[list[Todo]], None]] = []
        self._load()

    # ── Public API ────────────────────────────────────────────────────

    def create_todo(self, content: str, active_form: str) -> Todo:
        """Append a new pending todo and notify listeners."""
        todo = Todo(
            id=str(uuid.uuid4())[:8],
            content=content,
            active_form=active_form or content,
            status="pending",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        self._todos = [*self._todos, todo]
        self._save()
        self._notify()
        return todo

    def update_todo(self, todo_id: str, status: TodoStatus) -> None:
        """Update a single todo's status, enforcing single in_progress."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status: {status!r}")
        now = datetime.now().isoformat()
        new_todos: list[Todo] = []
        for t in self._todos:
            if t.id == todo_id:
                new_todos.append(Todo(
                    id=t.id, content=t.content, active_form=t.active_form,
                    status=status,
                    created_at=t.created_at, updated_at=now,
                ))
            elif status == "in_progress" and t.status == "in_progress":
                # Demote the previously active todo back to pending
                new_todos.append(Todo(
                    id=t.id, content=t.content, active_form=t.active_form,
                    status="pending",
                    created_at=t.created_at, updated_at=now,
                ))
            else:
                new_todos.append(t)
        self._todos = new_todos
        self._save()
        self._notify()

    def set_todos(self, todos_spec: list[dict]) -> None:
        """Replace the entire todo list (batch call from TodoWrite skill).

        Reuses existing IDs when content matches to avoid flicker.
        Enforces the single in_progress constraint.
        """
        now = datetime.now().isoformat()
        existing_by_content = {t.content: t for t in self._todos}
        new_todos: list[Todo] = []
        has_in_progress = False

        for spec in todos_spec:
            content = spec.get("content", "").strip()
            if not content:
                continue
            active_form = (
                spec.get("activeForm") or spec.get("active_form") or content
            ).strip()
            status = spec.get("status", "pending")
            if status not in _VALID_STATUSES:
                status = "pending"

            # Enforce single in_progress
            if status == "in_progress":
                if has_in_progress:
                    status = "pending"
                else:
                    has_in_progress = True

            old = existing_by_content.get(content)
            new_todos.append(Todo(
                id=old.id if old else str(uuid.uuid4())[:8],
                content=content,
                active_form=active_form,
                status=status,
                created_at=old.created_at if old else now,
                updated_at=now,
            ))

        self._todos = new_todos
        self._save()
        self._notify()

    def get_todos(self) -> list[Todo]:
        return list(self._todos)

    def clear_completed(self) -> int:
        """Remove completed todos. Returns count removed."""
        before = len(self._todos)
        self._todos = [t for t in self._todos if t.status != "completed"]
        removed = before - len(self._todos)
        if removed:
            self._save()
            self._notify()
        return removed

    def add_listener(self, fn: Callable[[list[Todo]], None]) -> None:
        """Register a callback invoked on every change."""
        self._listeners = [*self._listeners, fn]

    # ── Internal ──────────────────────────────────────────────────────

    def _notify(self) -> None:
        todos = list(self._todos)
        for fn in self._listeners:
            try:
                fn(todos)
            except Exception:
                logger.debug("TodoManager listener error", exc_info=True)

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps([asdict(t) for t in self._todos], indent=2))
        except Exception:
            logger.debug("TodoManager save failed", exc_info=True)

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            data = json.loads(self._path.read_text())
            self._todos = [Todo(**item) for item in data]
        except Exception:
            logger.debug("TodoManager load failed", exc_info=True)


# ── Module-level registry ─────────────────────────────────────────────

_registry: dict[str, TodoManager] = {}


def get_todo_manager(data_dir: Path, user_id: str) -> TodoManager:
    """Get or create the TodoManager for a given user."""
    key = f"{data_dir}:{user_id}"
    if key not in _registry:
        _registry[key] = TodoManager(data_dir, user_id)
    return _registry[key]
