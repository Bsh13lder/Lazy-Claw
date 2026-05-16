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
    """Start a task that runs in the background.

    Per-turn re-registration (see :mod:`lazyclaw.runtime.agent`) injects
    ``callback`` and ``fanout_group_id`` so a fresh group identity is
    minted every TAOR turn. When the brain calls this tool 2+ times in
    one turn — typically because the dispatch_subagents same-shape
    rejector bounced its fan-out — every sibling shares the group ID,
    individual per-task pushes get suppressed, and ONE consolidated
    brain reply fires once the last sibling settles.
    """

    def __init__(
        self,
        config: Config | None = None,
        callback: AgentCallback | None = None,
        fanout_group_id: str | None = None,
        chat_session_id: str | None = None,
    ) -> None:
        self._config = config
        self._task_runner: TaskRunner | None = None
        self._callback = callback
        self._fanout_group_id = fanout_group_id
        self._chat_session_id = chat_session_id
        # Nesting depth of the agent that owns this skill instance. The
        # agent runtime injects this alongside `_task_runner` on every
        # turn (see runtime.agent ~line 1810). Forwarded to
        # task_runner.submit so MAX_TASK_DEPTH bounds nested fan-out
        # cleanly instead of relying on the 3-strikes failsafe.
        self._caller_depth: int = 0

    @property
    def name(self) -> str:
        return "run_background"

    @property
    def display_name(self) -> str:
        return "Run in Background"

    @property
    def description(self) -> str:
        return (
            "Start a long-running action that should continue while the user keeps chatting. "
            "The background agent has ALL your tools (browser, web_search, memory, etc). "
            "USE ONLY WHEN: the task takes >30s AND is a concrete action "
            "(send a message, submit a form, apply to a job, run a scraping job, "
            "execute a multi-step browser flow). "
            "DO NOT USE FOR: diagnostic questions ('what is the problem?', "
            "'why doesn't X work?', 'check the logs', 'explain this config'), "
            "one-shot lookups, short answers, summaries of what you just did, "
            "or anything a single tool call could answer. Answer those inline instead. "
            "NOT for monitoring/watching — use watch_site or watch_messages. "
            "Results stream back into THIS chat when done (and to Telegram). "
            "Runtime supports up to 10 concurrent background tasks per user — "
            "don't hold back on fan-out: if the user asks for 5 independent "
            "long-running actions, start all 5 in parallel."
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
                "project_tag": {
                    "type": "string",
                    "description": (
                        "Optional project group tag for the Code Specialist UI "
                        "(format: 'upwork:job-XXX', 'reddit:dm', 'gig:abc123', "
                        "'user_request', etc.). Lets the user filter / group "
                        "background runs by what business workflow they're for. "
                        "Empty = untagged user-driven request."
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
        project_tag = (params.get("project_tag") or "").strip()

        try:
            # source="brain" + fanout_group_id route this submit through
            # TaskRunner's fan-out group bucketing. If the brain calls
            # run_background only once in the turn, the group will have
            # 1 entry and TaskRunner._consolidate degrades to the legacy
            # per-task push automatically.
            task_id = await self._task_runner.submit(
                user_id=user_id,
                instruction=instruction,
                name=name,
                callback=self._callback,
                source="brain",
                fanout_group_id=self._fanout_group_id,
                chat_session_id=self._chat_session_id,
                project_tag=project_tag,
                caller_depth=self._caller_depth,
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
