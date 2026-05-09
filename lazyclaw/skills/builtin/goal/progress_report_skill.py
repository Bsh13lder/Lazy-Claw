"""goal_progress_report — Markdown digest of every active goal.

Designed to fit cleanly under the slim heartbeat path so a user-wired
``[GOAL_PROGRESS]`` cron costs ~5k tokens instead of a full 40k turn.
The agent's daily ``ScheduleJobSkill`` instruction is::

    cron_expression: "0 9 * * *"
    instruction: "[GOAL_PROGRESS] all"

The ``[GOAL_PROGRESS]`` prefix flips ``runtime.agent`` onto the slim
context branch (no SOUL.md, no capabilities, only the recall tools);
the brain then calls THIS skill to produce the body of the message.
"""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class GoalProgressReportSkill(BaseSkill):
    """One-shot Markdown digest covering every non-terminal goal."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "goal_progress_report"

    @property
    def display_name(self) -> str:
        return "Goal Progress Report"

    @property
    def description(self) -> str:
        return (
            "Markdown digest of every active goal: title, status, steps_done/"
            "steps_total, blocked_on, last_action. Designed to be wired to a "
            "user-created daily cron via the existing ScheduleJobSkill — no "
            "auto-cron is created. NO LLM call."
        )

    @property
    def category(self) -> str:
        return "orchestration"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "verbose": {
                    "type": "boolean",
                    "description": (
                        "If True, render full per-goal cards (steps, "
                        "questions, risks). Default False = compact rows."
                    ),
                    "default": False,
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.runtime.goal_executor import (
            GoalRepository, GoalStatus, _format_one, _format_one_short,
        )
        verbose = bool(params.get("verbose", False))

        repo = GoalRepository(self._config)
        goals = await repo.list(
            user_id,
            statuses=(
                GoalStatus.DRAFTING, GoalStatus.AWAITING_USER_INFO,
                GoalStatus.EXECUTING, GoalStatus.BLOCKED,
            ),
            limit=50,
        )
        if not goals:
            return "No active goals — nothing to report."

        # Headline counts
        by_status: dict[str, int] = {}
        for g in goals:
            by_status[g.status.value] = by_status.get(g.status.value, 0) + 1
        head_bits = [f"{n} {label}" for label, n in sorted(by_status.items())]
        lines = [
            f"**Daily goal report — {len(goals)} active** ({', '.join(head_bits)})",
            "",
        ]
        if verbose:
            for g in goals:
                lines.append(_format_one(g))
                lines.append("")
        else:
            for g in goals:
                lines.append(_format_one_short(g))
        return "\n".join(lines).rstrip()
