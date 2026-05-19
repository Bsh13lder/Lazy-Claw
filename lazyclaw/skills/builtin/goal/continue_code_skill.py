"""continue_code_goal — append a turn to an EXECUTING code goal (same worker session).

The brain calls this INSTEAD of ``run_background`` when the user wants
more work done on an already-running code goal ("now add tests", "fix
the city filter", "deploy it"). Reuses the goal's stored
``code_session_id`` so the Code Specialist's worker brain picks up
exactly where it left off — no re-priming the contract scope, no
re-explaining the workspace, no cold start.

Routing rule (also enforced via SOUL.md): if an active code Goal
exists for the user, code-work follow-ups MUST go through this skill,
NOT ``run_background`` — ``run_background`` launches a Claude CLI
that's ``--disallowedTools Bash,Read,Edit,Write,Glob,Grep,...`` and
will hang silently because it has no file-system tools (see
MEMORY → `feedback_code_tasks_via_claude_code_mcp`).
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class ContinueCodeGoalSkill(BaseSkill):
    """Append a turn to an EXECUTING code goal."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "continue_code_goal"

    @property
    def display_name(self) -> str:
        return "Continue Code Goal"

    @property
    def description(self) -> str:
        return (
            "Continue an EXECUTING code goal with a new instruction — "
            "reuses the goal's persistent claude-code session so the "
            "worker remembers prior turns (recon, files written, decisions "
            "made). Use for follow-up code work like 'now add tests', "
            "'fix the city filter bug', 'deploy it', 'refactor the queue'. "
            "NEVER use run_background for these — run_background's Claude "
            "subprocess can't write files. Pass goal_id from list_goals or "
            "the latest start_goal response."
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
                        "Goal ID to continue (first 8+ hex chars accepted "
                        "for ergonomics; full id also works). Must be an "
                        "EXECUTING or BLOCKED code-tagged goal."
                    ),
                },
                "instruction": {
                    "type": "string",
                    "description": (
                        "The new turn instruction in plain English — what "
                        "the worker should do next ('add a city filter for "
                        "Oakland-Hayward-San-Leandro', 'write tests for the "
                        "BPO accept flow', 'deploy to launchd'). The worker "
                        "already has the project context from prior turns, "
                        "so DON'T re-explain the original brief here."
                    ),
                },
            },
            "required": ["goal_id", "instruction"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        goal_id_raw = (params.get("goal_id") or "").strip()
        instruction = (params.get("instruction") or "").strip()
        if not goal_id_raw:
            return "Missing required field: goal_id."
        if not instruction:
            return "Missing required field: instruction."

        from lazyclaw.runtime.goal_executor import (
            GoalExecutor, GoalRepository, GoalStatus, InvalidGoalTransition,
        )

        repo = GoalRepository(self._config)

        # Resolve short id prefixes — UX nicety so brain can pass `abc12345`
        # from a /goal list response without copy-pasting the full uuid.
        goal_id = goal_id_raw
        if len(goal_id_raw) < 32:
            candidates = await repo.list(user_id, limit=200)
            matches = [g for g in candidates if g.id.startswith(goal_id_raw)]
            if not matches:
                return (
                    f"No goal found for prefix `{goal_id_raw}`. Use "
                    f"`list_goals` to see active goals."
                )
            if len(matches) > 1:
                return (
                    f"Ambiguous prefix `{goal_id_raw}` — matches "
                    f"{len(matches)} goals. Pass a longer prefix."
                )
            goal_id = matches[0].id

        executor = GoalExecutor(self._config)
        try:
            goal = await executor.continue_code(user_id, goal_id, instruction)
        except LookupError:
            return f"Goal `{goal_id[:8]}` not found."
        except InvalidGoalTransition as exc:
            return f"Cannot continue: {exc}"
        except ValueError as exc:
            return f"Invalid request: {exc}"
        except Exception as exc:
            logger.exception("continue_code_goal failed")
            return f"Failed to continue goal: {exc}"

        return (
            f"Continuing goal `{goal.id[:8]}` — claude-code worker is "
            f"picking up the same session. Will report back when done.\n\n"
            f"_Status: {goal.status.value} · last: {goal.last_action}_"
        )
