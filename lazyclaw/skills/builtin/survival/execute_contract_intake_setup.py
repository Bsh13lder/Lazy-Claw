"""execute_contract_intake_setup — manually re-run the auto-setup phase.

Wraps :func:`lazyclaw.runtime.contract_intake_executor.dispatch_contract_intake`
as an NL skill so the user can re-trigger setup for a goal that landed
in ``BLOCKED`` after a partial failure (Ollama down during parsing,
template-store transient error, etc.).

Normal path: the executor auto-fires when ``answer_goal_questions``
transitions a goal to ``EXECUTING`` (via Goal Executor's
``_DEFAULT_DISPATCH_BY_SLUG`` registry). This skill is the escape
hatch when the auto-fire failed and the user wants to retry.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class ExecuteContractIntakeSetupSkill(BaseSkill):
    """Re-run the contract auto-setup phase for a specific goal."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "execute_contract_intake_setup"

    @property
    def description(self) -> str:
        return (
            "Re-run the auto-setup phase (vault credentials, register "
            "browser account, add live-host, register watcher, save "
            "accept template, save LazyBrain doc, escalate login prompt) "
            "for an Upwork contract goal that's already had its "
            "clarifying questions answered. Use when a goal landed in "
            "BLOCKED after a partial failure (Ollama timeout, transient "
            "DB error). The executor is idempotent — safe to retry."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": (
                        "ID of the contract intake Goal "
                        "(account_slug='upwork'). Get it from goal_status "
                        "or list_goals."
                    ),
                },
            },
            "required": ["goal_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if self._config is None:
            return "Error: config not available."
        goal_id = (params.get("goal_id") or "").strip()
        if not goal_id:
            return (
                "goal_id is required. Use `list_goals(status='blocked')` "
                "to find the goal you want to retry."
            )

        from lazyclaw.runtime.contract_intake_executor import (
            dispatch_contract_intake,
        )
        from lazyclaw.runtime.goal_executor import GoalExecutor, GoalStatus

        executor = GoalExecutor(self._config)
        goal = await executor._repo.get(user_id, goal_id)
        if goal is None:
            return f"Goal {goal_id} not found for this user."
        if goal.account_slug != "upwork":
            return (
                f"Goal {goal_id} has account_slug={goal.account_slug!r}; "
                "only 'upwork' goals are handled by this executor."
            )
        if goal.status not in {GoalStatus.EXECUTING, GoalStatus.BLOCKED}:
            return (
                f"Goal {goal_id} is in {goal.status.value}; executor expects "
                "EXECUTING or BLOCKED."
            )

        # If currently BLOCKED, transition to EXECUTING so dispatch runs.
        if goal.status == GoalStatus.BLOCKED:
            from dataclasses import replace
            import time as _time
            from lazyclaw.runtime.goal_executor import _assert_transition
            try:
                _assert_transition(GoalStatus.BLOCKED, GoalStatus.EXECUTING)
                goal = await executor._repo.update(replace(
                    goal,
                    status=GoalStatus.EXECUTING,
                    last_action="manual retry via execute_contract_intake_setup",
                    last_progress_at=_time.time(),
                    blocked_on=None,
                ))
            except Exception as exc:
                logger.exception("retry transition failed")
                return f"Could not transition {goal_id} to EXECUTING: {exc}"

        return await dispatch_contract_intake(
            goal, config=self._config, registry=self._registry,
        )
