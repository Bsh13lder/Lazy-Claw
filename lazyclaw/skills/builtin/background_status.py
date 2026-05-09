"""Brain-facing query for live background-task progress.

Closes the on-demand progress-check gap: today the brain can fire
``run_background(...)`` but has no built-in way to ask "what's running
right now?" mid-conversation. With this skill the brain — whichever
model is configured (Claude, MiniMax, future) — can answer the user's
"how's that gig going?" question without waiting for the Telegram
completion ping.

Reads live state from ``TeamLead`` (`current_tool`, `recent_tools`,
`step_count`, `phase`, `project_tag`, `started_at`). Zero LLM cost —
this is a state-read skill, no model call.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from lazyclaw.skills.base import BaseSkill

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.runtime.team_lead import TeamLead

logger = logging.getLogger(__name__)


class BackgroundStatusSkill(BaseSkill):
    """Read-only progress check for running background tasks.

    The brain (any LLM, not Claude-specific) calls this when the user
    asks "what's the agent doing?" / "how's that task going?" / etc.
    Returns a structured snapshot with current_tool + recent_tools so
    the brain can produce an honest progress report instead of guessing.
    """

    def __init__(self, config: Config | None = None) -> None:
        self._config = config
        self._team_lead: TeamLead | None = None

    def attach_team_lead(self, team_lead: "TeamLead") -> None:
        """Wired by the runtime at startup. Without this the skill
        returns 'no team lead attached' (still valid — never throws)."""
        self._team_lead = team_lead

    @property
    def name(self) -> str:
        return "background_status"

    @property
    def description(self) -> str:
        return (
            "Query live progress of running background tasks. Use this "
            "when the user asks 'how's it going?' / 'what's the agent "
            "doing?' / 'progress on that gig?' or any time you (the "
            "brain) need to report status without waiting for the task "
            "to finish. Returns: list of running tasks with name, "
            "project_tag (e.g. 'upwork:job-XXX'), elapsed time, current "
            "tool, recent tools, step count. Zero cost — pure state read, "
            "no LLM call. ALWAYS prefer this over guessing or saying "
            "'still running, please wait'."
        )

    @property
    def category(self) -> str:
        return "core"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "Optional task ID prefix to filter to one task. "
                        "Omit to get all running tasks for the current user."
                    ),
                },
                "project_tag": {
                    "type": "string",
                    "description": (
                        "Optional project filter (e.g. 'upwork', 'reddit'). "
                        "Returns only tasks whose project_tag starts with "
                        "this prefix. Omit for all."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if self._team_lead is None:
            return "Background status unavailable (TeamLead not attached)."

        active = list(self._team_lead._active.values())
        # Filter to the calling user
        active = [
            t for t in active
            if self._team_lead._task_users.get(t.task_id, "") == user_id
            and t.lane in ("background", "specialist")
            and t.status == "running"
        ]

        task_id_prefix = (params.get("task_id") or "").strip()
        if task_id_prefix:
            active = [t for t in active if t.task_id.startswith(task_id_prefix)]

        project_filter = (params.get("project_tag") or "").strip().lower()
        if project_filter:
            active = [
                t for t in active
                if (t.project_tag or "").lower().startswith(project_filter)
            ]

        if not active:
            return "No background tasks running."

        now = time.monotonic()
        lines = [f"{len(active)} background task(s) running:"]
        for t in active:
            elapsed = int(now - t.started_at)
            mm, ss = divmod(elapsed, 60)
            elapsed_str = f"{mm}m {ss}s" if mm else f"{ss}s"
            project = t.project_tag or "user_request"
            current = t.current_tool or t.phase or "(starting)"
            recent = list(t.recent_tools)[-5:]
            recent_str = " → ".join(recent) if recent else "(none yet)"
            lines.append(
                f"\n• {t.name} [{t.task_id[:8]}]\n"
                f"    project: {project}\n"
                f"    elapsed: {elapsed_str}, steps: {t.step_count}\n"
                f"    current tool: {current}\n"
                f"    recent: {recent_str}"
            )
        return "\n".join(lines)
