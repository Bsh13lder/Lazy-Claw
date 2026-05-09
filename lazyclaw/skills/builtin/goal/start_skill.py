"""start_goal — draft an autonomous goal and ask all clarifying questions in one batch."""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class StartGoalSkill(BaseSkill):
    """Kick off an autonomous goal.

    The brain provides a high-level title ("sell my product on Hirossa")
    and optionally hints (existing context). The skill:
      1. Pulls what LazyBrain knows about the user's business.
      2. Asks fix_plan to draft a step-list + clarifying questions.
      3. Persists a Goal row.
      4. Returns either an EXECUTING confirmation OR a single batch-ask
         card listing every required input upfront — never drip-asking.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "start_goal"

    @property
    def display_name(self) -> str:
        return "Start Goal"

    @property
    def description(self) -> str:
        return (
            "Start an autonomous high-level goal (e.g. 'sell my product on "
            "Hirossa', 'post the same campaign across both Reddit accounts'). "
            "The agent drafts a plan, surfaces every clarifying question in "
            "ONE batch, then dispatches to the browser specialist on approval."
        )

    @property
    def category(self) -> str:
        return "orchestration"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Short title of the goal — what outcome the user wants "
                        "(e.g. 'sell product on hirossa.com')."
                    ),
                },
                "hints": {
                    "type": "object",
                    "description": (
                        "Optional key/value hints that disambiguate the goal "
                        "(account email, target site, branding tone, deadline)."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "account_slug": {
                    "type": "string",
                    "description": (
                        "Optional registered browser account slug to use for "
                        "this goal. Leave blank for the default profile."
                    ),
                },
            },
            "required": ["title"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        title = (params.get("title") or "").strip()
        if not title:
            return "Missing required field: title."

        hints_raw = params.get("hints") or {}
        hints = {str(k): str(v) for k, v in hints_raw.items() if v} \
            if isinstance(hints_raw, dict) else {}

        account_slug = (params.get("account_slug") or "").strip() or None

        from lazyclaw.runtime.goal_executor import GoalExecutor, GoalStatus
        executor = GoalExecutor(self._config)
        goal = await executor.start(
            user_id, title, hints=hints, account_slug=account_slug,
        )

        from lazyclaw.runtime.goal_executor import _format_one
        body = _format_one(goal)

        if goal.status == GoalStatus.AWAITING_USER_INFO:
            return (
                f"Goal `{goal.id[:8]}` drafted — answers needed before "
                f"execution.\n\n{body}\n\n"
                f"Reply with `answer_goal_questions` (or just answer in chat — "
                f"the agent will route)."
            )
        if goal.status == GoalStatus.EXECUTING:
            return f"Goal `{goal.id[:8]}` started — dispatching now.\n\n{body}"
        if goal.status == GoalStatus.DRAFTING:
            return (
                f"Goal `{goal.id[:8]}` saved in DRAFTING — the planner LLM was "
                f"unavailable. Retry once the worker model is back up.\n\n{body}"
            )
        return body
