"""abort_goal — terminate a goal (terminal, user-triggered)."""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class AbortGoalSkill(BaseSkill):
    """Stop a running goal. Already-terminal goals are no-ops."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "abort_goal"

    @property
    def display_name(self) -> str:
        return "Abort Goal"

    @property
    def description(self) -> str:
        return (
            "Cancel a goal. Status moves to ABORTED (terminal). Already-"
            "terminal goals are silently ignored — calling abort twice is safe."
        )

    @property
    def category(self) -> str:
        return "orchestration"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": (
                        "Goal ID (full or 8-char prefix) to abort."
                    ),
                },
            },
            "required": ["goal_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        goal_id_raw = (params.get("goal_id") or "").strip()
        if not goal_id_raw:
            return "Missing required field: goal_id."

        from lazyclaw.runtime.goal_executor import GoalExecutor, GoalRepository
        from lazyclaw.skills.builtin.goal.answer_skill import _resolve_goal_id

        repo = GoalRepository(self._config)
        full_id = await _resolve_goal_id(repo, user_id, goal_id_raw)
        if full_id is None:
            return f"No goal matching `{goal_id_raw}`."

        executor = GoalExecutor(self._config)
        goal = await executor.abort(user_id, full_id)
        return f"Goal `{goal.id[:8]}` is now `{goal.status.value}`."
