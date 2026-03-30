"""Background task skill — run tasks in parallel while user keeps chatting.

The agent calls this when a task should run independently. The task gets
a fresh Agent instance, executes with all tools available, and notifies
the user via Telegram/CLI when done.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lazyclaw.skills.base import BaseSkill

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.runtime.callbacks import AgentCallback
    from lazyclaw.runtime.task_runner import TaskRunner

logger = logging.getLogger(__name__)


class RunBackgroundSkill(BaseSkill):
    """Start a task that runs in the background."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config
        self._task_runner: TaskRunner | None = None
        self._callback: AgentCallback | None = None

    @property
    def name(self) -> str:
        return "run_background"

    @property
    def display_name(self) -> str:
        return "Run in Background"

    @property
    def description(self) -> str:
        return (
            "Start a one-shot task that runs in the background while you keep chatting. "
            "The background agent has ALL your tools (browser, web_search, memory, etc). "
            "Use for: sending messages, web research, checking prices, "
            "any task that takes time. You'll be notified on Telegram when it's done. "
            "NOT for monitoring/watching — use watch_site or watch_messages instead. "
            "Can run up to 2 background tasks at once."
        )

    @property
    def category(self) -> str:
        return "general"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": (
                        "Detailed instruction for the background agent. "
                        "Be specific — include URLs, names, numbers, exactly what to do."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Short name for tracking (e.g. 'whatsapp_msg', 'btc_check', "
                        "'email_draft'). Shown in notifications and /tasks."
                    ),
                },
            },
            "required": ["instruction"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._task_runner:
            return "Error: background task runner not configured"

        instruction = params.get("instruction", "").strip()
        if not instruction:
            return "Error: instruction is required"

        name = params.get("name", "").strip() or None

        try:
            task_id = await self._task_runner.submit(
                user_id=user_id,
                instruction=instruction,
                name=name,
                callback=self._callback,
            )
        except RuntimeError as exc:
            return f"Cannot start background task: {exc}"

        display_name = name or task_id[:8]
        running = self._task_runner.list_running(user_id)
        count = len(running)

        return (
            f"Background task '{display_name}' started (id: {task_id[:8]}). "
            f"You have {count} task{'s' if count != 1 else ''} running. "
            f"I'll notify you when it's done."
        )
