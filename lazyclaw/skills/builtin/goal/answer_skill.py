"""answer_goal_questions — submit batch-ask answers to a drafted goal."""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class AnswerGoalQuestionsSkill(BaseSkill):
    """Hand the goal executor the answers it asked for.

    Partial answers are accepted: the goal stays in AWAITING_USER_INFO
    with the remaining questions intact. Once every question is
    answered the goal transitions to EXECUTING and dispatch fires.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "answer_goal_questions"

    @property
    def display_name(self) -> str:
        return "Answer Goal Questions"

    @property
    def description(self) -> str:
        return (
            "Submit answers to the clarifying questions a drafted goal asked. "
            "Pass answers as a {question: answer} map keyed by the question text "
            "verbatim. Partial answers are fine — the goal stays awaiting until "
            "every question is covered."
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
                        "The goal ID returned by `start_goal` (full or 8-char prefix)."
                    ),
                },
                "answers": {
                    "type": "object",
                    "description": "Map of question text → answer. Strings only.",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["goal_id", "answers"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        goal_id = (params.get("goal_id") or "").strip()
        answers_raw = params.get("answers") or {}
        if not goal_id:
            return "Missing required field: goal_id."
        if not isinstance(answers_raw, dict) or not answers_raw:
            return "Missing required field: answers (must be a non-empty object)."

        from lazyclaw.runtime.goal_executor import (
            GoalExecutor, GoalRepository, GoalStatus, InvalidGoalTransition,
        )

        # Allow a short prefix so the user can paste the 8-char ID we render.
        repo = GoalRepository(self._config)
        full_id = await _resolve_goal_id(repo, user_id, goal_id)
        if full_id is None:
            return f"No goal matching `{goal_id}`."

        executor = GoalExecutor(self._config)
        try:
            goal = await executor.submit_answers(
                user_id, full_id,
                {str(k): str(v) for k, v in answers_raw.items()},
            )
        except InvalidGoalTransition as exc:
            return f"Cannot accept answers: {exc!s}"

        from lazyclaw.runtime.goal_executor import _format_one
        body = _format_one(goal)
        if goal.status == GoalStatus.EXECUTING:
            return f"All answers received — dispatching now.\n\n{body}"
        return f"Recorded {len(answers_raw)} answer(s); still awaiting:\n\n{body}"


async def _resolve_goal_id(repo, user_id: str, partial: str) -> str | None:
    """Accept full UUID hex or 8-char prefix. Returns the full ID, or None."""
    if len(partial) >= 32:
        goal = await repo.get(user_id, partial)
        return goal.id if goal else None
    candidates = await repo.list(user_id, statuses=None, limit=200)
    matches = [g for g in candidates if g.id.startswith(partial)]
    if len(matches) == 1:
        return matches[0].id
    return None
