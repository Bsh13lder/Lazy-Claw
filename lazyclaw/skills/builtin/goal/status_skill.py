"""goal_status — render progress for one goal or all active goals (no LLM)."""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class GoalStatusSkill(BaseSkill):
    """Show progress on a goal. Pass ``goal_id`` for one, or omit for all active."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "goal_status"

    @property
    def display_name(self) -> str:
        return "Goal Status"

    @property
    def description(self) -> str:
        return (
            "Show progress for one goal (pass goal_id) or a digest of every "
            "active goal (omit goal_id). No LLM call — instant."
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
                "goal_id": {
                    "type": "string",
                    "description": (
                        "Optional goal ID (full or 8-char prefix). Omit to "
                        "render the digest of every non-terminal goal."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        goal_id_raw = (params.get("goal_id") or "").strip()
        from lazyclaw.runtime.goal_executor import GoalExecutor, GoalRepository

        executor = GoalExecutor(self._config)
        if not goal_id_raw:
            return await executor.status_block(user_id)

        repo = GoalRepository(self._config)
        from lazyclaw.skills.builtin.goal.answer_skill import _resolve_goal_id
        full_id = await _resolve_goal_id(repo, user_id, goal_id_raw)
        if full_id is None:
            return f"No goal matching `{goal_id_raw}`."
        return await executor.status_block(user_id, full_id)
