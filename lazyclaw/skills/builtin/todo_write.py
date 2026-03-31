"""TodoWrite skill — agent task planning and real-time progress tracking.

The agent calls this tool at the START of any task with 3+ steps, passing
the full plan. It then calls it again to update statuses as work progresses.
Only ONE todo can be in_progress at a time.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lazyclaw.skills.base import BaseSkill

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.runtime.todo_manager import TodoManager

logger = logging.getLogger(__name__)


class TodoWriteSkill(BaseSkill):
    """Plan and track task progress with a real-time todo list in the TUI."""

    def __init__(self, config: "Config | None" = None) -> None:
        self._config = config
        self._todo_manager: "TodoManager | None" = None

    @property
    def name(self) -> str:
        return "todo_write"

    @property
    def display_name(self) -> str:
        return "TodoWrite"

    @property
    def description(self) -> str:
        return (
            "Create and update a task plan for multi-step operations. "
            "MANDATORY for any task with 3 or more steps. "
            "Call ONCE at the START with the full plan (all todos as pending), "
            "then call again to update statuses as you work: "
            "mark in_progress BEFORE starting each step, "
            "mark completed IMMEDIATELY after finishing. "
            "Only ONE todo can be in_progress at a time."
        )

    @property
    def category(self) -> str:
        return "general"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": (
                        "Complete todo list. Replaces current list. "
                        "Each entry needs content (imperative), activeForm (continuous), "
                        "and status (pending|in_progress|completed)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Imperative: 'Run tests', 'Update config file'",
                            },
                            "activeForm": {
                                "type": "string",
                                "description": "Continuous: 'Running tests', 'Updating config file'",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["content", "activeForm", "status"],
                    },
                },
            },
            "required": ["todos"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        todos_spec = params.get("todos", [])
        if not todos_spec:
            return "Error: todos list is required"

        manager = self._resolve_manager(user_id)
        if manager is None:
            return "TodoWrite unavailable (no data directory configured)"

        manager.set_todos(todos_spec)
        todos = manager.get_todos()

        in_progress = [t for t in todos if t.status == "in_progress"]
        pending = [t for t in todos if t.status == "pending"]
        completed = [t for t in todos if t.status == "completed"]

        parts = [f"Task plan: {len(todos)} steps"]
        if in_progress:
            parts.append(f"Now: {in_progress[0].active_form}")
        if pending:
            parts.append(f"Next: {pending[0].content}")
        parts.append(f"({len(completed)}/{len(todos)} done)")
        return " — ".join(parts)

    def _resolve_manager(self, user_id: str) -> "TodoManager | None":
        if self._todo_manager is not None:
            return self._todo_manager
        if self._config is not None:
            from lazyclaw.runtime.todo_manager import get_todo_manager
            return get_todo_manager(self._config.database_dir, user_id)
        return None
