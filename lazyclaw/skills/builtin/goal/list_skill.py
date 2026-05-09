"""list_goals — table of recent goals (active + recently completed)."""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class ListGoalsSkill(BaseSkill):
    """Recent goals in a compact table, newest first."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_goals"

    @property
    def display_name(self) -> str:
        return "List Goals"

    @property
    def description(self) -> str:
        return (
            "List the user's goals — active and recently terminal. Returns a "
            "compact table with id-prefix, title, status, steps, last action."
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
                "include_terminal": {
                    "type": "boolean",
                    "description": (
                        "If True, include DONE / FAILED / ABORTED goals. "
                        "Default False = active goals only."
                    ),
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 20).",
                    "default": 20,
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        include_terminal = bool(params.get("include_terminal", False))
        try:
            limit = max(1, min(int(params.get("limit", 20)), 100))
        except (TypeError, ValueError):
            limit = 20

        from lazyclaw.runtime.goal_executor import GoalRepository, GoalStatus, _format_one_short

        statuses = None if include_terminal else (
            GoalStatus.DRAFTING, GoalStatus.AWAITING_USER_INFO,
            GoalStatus.EXECUTING, GoalStatus.BLOCKED,
        )
        repo = GoalRepository(self._config)
        goals = await repo.list(user_id, statuses=statuses, limit=limit)
        if not goals:
            return "_No goals._" if include_terminal else "_No active goals._"
        lines = [_format_one_short(g) for g in goals]
        return "\n".join(lines)
