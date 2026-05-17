"""Goals API — surface the Goal Executor's intake-and-dispatch flow.

Today this only powers the **Code Specialist intake** modal on the Web
UI: user types a free-text "build me X" request → the AI drafts a plan
and asks clarifying questions in ONE batch → user answers → goal
transitions to EXECUTING and the registered ``code`` dispatch callback
fires the Code Specialist (which routes to claude-code MCP, writes
files to ``/workspace/<tag>/<goal_id>/<task_id>/``).

The same endpoints generalize to other goal kinds; for now we only
expose the ``code`` slug because the contract-intake flow already has
its own Telegram-driven entry point.

Endpoints (all under ``/api/goals``):

  POST /code-task             → start a new code goal
  POST /{id}/answer           → submit answers for the questions
  GET  /{id}                  → poll current status + remaining questions
  POST /{id}/abort            → cancel a goal (idempotent)
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.runtime.goal_executor import (
    Goal,
    GoalExecutor,
    GoalRepository,
    GoalStatus,
    InvalidGoalTransition,
)

logger = logging.getLogger(__name__)

_config = load_config()

router = APIRouter(prefix="/api/goals", tags=["goals"])


# ── Request / response shapes ──────────────────────────────────────


class CodeTaskStartBody(BaseModel):
    """Initial intake payload from the Web UI's NewCodeTaskModal.

    ``title`` is the free-text "I want to build…" the user typed.
    ``project_tag`` is optional bucket label (e.g. ``upwork:job-42``,
    ``personal``) — drives the CodeSpecialist page grouping AND becomes
    the first path segment under /workspace/ so the user finds their
    files easily on Desktop.
    """
    title: str = Field(min_length=4, max_length=2000)
    project_tag: str | None = Field(default=None, max_length=120)
    hints: dict[str, str] | None = None


class AnswerBody(BaseModel):
    """User-submitted answers keyed by the question text (verbatim)."""
    answers: dict[str, str]


class GoalView(BaseModel):
    """JSON-safe view of a Goal. Strip internal-only fields (cancel
    tokens, raw plan envelope) — caller doesn't need them."""
    id: str
    status: str
    title: str
    summary: str
    work_type: str | None = None
    account_slug: str | None = None
    confidence: str
    questions_pending: list[str]
    answers: dict[str, str]
    risks: list[str]
    plan: list[dict]
    blocked_on: str | None = None
    last_action: str
    is_terminal: bool


def _view(goal: Goal) -> GoalView:
    return GoalView(
        id=goal.id,
        status=goal.status.value,
        title=goal.title,
        summary=goal.summary,
        work_type=goal.work_type,
        account_slug=goal.account_slug,
        confidence=goal.confidence,
        questions_pending=list(goal.questions_pending),
        answers=dict(goal.answers),
        risks=list(goal.risks),
        plan=[asdict(s) for s in goal.plan],
        blocked_on=goal.blocked_on,
        last_action=goal.last_action,
        is_terminal=goal.is_terminal(),
    )


def _executor() -> GoalExecutor:
    return GoalExecutor(_config, GoalRepository(_config))


# ── Endpoints ──────────────────────────────────────────────────────


@router.post("/code-task", response_model=GoalView)
async def start_code_task(
    body: CodeTaskStartBody,
    user: User = Depends(get_current_user),
):
    """Draft a code goal — the AI plans + asks clarifying questions.

    Returns the freshly-created goal. If the goal lands in
    ``AWAITING_USER_INFO`` (the common case for any non-trivial brief),
    the response carries the questions the modal should render. If it
    lands directly in ``EXECUTING`` (rare — the brief was crystal-clear
    and needed no follow-up) the dispatch already fired and the user
    can watch the Code Specialist page populate.
    """
    try:
        goal = await _executor().start(
            user_id=user.id,
            title=body.title,
            hints=body.hints,
            account_slug="code",
            work_type=(body.project_tag or None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("start_code_task failed for user %s", user.id)
        raise HTTPException(status_code=500, detail=f"goal draft failed: {exc!s}")
    return _view(goal)


@router.post("/{goal_id}/answer", response_model=GoalView)
async def submit_goal_answers(
    goal_id: str,
    body: AnswerBody,
    user: User = Depends(get_current_user),
):
    """Submit (possibly partial) answers. Triggers dispatch on the last
    question. Re-callable while ``questions_pending`` is non-empty."""
    try:
        goal = await _executor().submit_answers(
            user_id=user.id, goal_id=goal_id, answers=body.answers,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="goal not found")
    except InvalidGoalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.exception("submit_goal_answers failed for goal %s", goal_id)
        raise HTTPException(status_code=500, detail=f"answer submit failed: {exc!s}")
    return _view(goal)


@router.get("/{goal_id}", response_model=GoalView)
async def get_goal(
    goal_id: str,
    user: User = Depends(get_current_user),
):
    """Poll the current goal state — modal uses this if the dispatch
    is slow or the user reopens the page mid-run."""
    repo = GoalRepository(_config)
    goal = await repo.get(user.id, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    return _view(goal)


@router.post("/{goal_id}/abort", response_model=GoalView)
async def abort_goal(
    goal_id: str,
    user: User = Depends(get_current_user),
):
    """User-triggered cancel. Idempotent for already-terminal goals."""
    try:
        goal = await _executor().abort(user.id, goal_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="goal not found")
    except Exception as exc:
        logger.exception("abort_goal failed for goal %s", goal_id)
        raise HTTPException(status_code=500, detail=f"abort failed: {exc!s}")
    return _view(goal)


@router.get("", response_model=list[GoalView])
async def list_goals(
    user: User = Depends(get_current_user),
    limit: int = 30,
):
    """Return recent goals (any status). Mostly used to show
    in-flight code goals on the CodeSpecialist page header."""
    repo = GoalRepository(_config)
    try:
        goals = await repo.list(user.id, limit=limit)
    except AttributeError:
        # Older GoalRepository didn't have list() — graceful empty.
        return []
    return [_view(g) for g in goals]
