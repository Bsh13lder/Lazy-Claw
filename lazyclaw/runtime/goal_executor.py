"""Goal Executor — autonomous high-level objectives on top of the existing planner stack.

The goal of this module is one thing: make ``start_goal("sell my product on Hirossa")``
do everything the user described — gather what LazyBrain knows about
their business, draft a concrete step plan, **batch every clarifying
question into one ask**, dispatch to the browser specialist, and report
progress on demand. No new architecture; just thin orchestration over:

- :func:`runtime.plan_research.gather_plan_research` — 4-lane zero-LLM context.
- :func:`runtime.fix_plan.build_fix_plan` — single brain call returning
  ``summary + steps + questions + risks + confidence``.
- :class:`runtime.dispatcher.AgentDispatcher` — SPECIALIST-lane execution.
- :class:`runtime.team_lead.TeamLead` — live progress without LLM.
- :func:`lazybrain.embeddings.semantic_search` — pull existing business memory.

Persistence: encrypted ``goals`` table (Fernet, AAD ``user:<id>:goals:title``
and ``…:goals:plan``). Survives restarts so ``/goal status`` doesn't need
the in-memory tracker to be alive.

State machine::

    DRAFTING ──► AWAITING_USER_INFO ──► EXECUTING ──► DONE
                                              │
                                              ├─► BLOCKED ──► EXECUTING (on user unblock)
                                              ├─► FAILED
                                              └─► ABORTED  (user-triggered, terminal)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

from lazyclaw.crypto.encryption import decrypt_field, encrypt_field, user_aad
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


# ── State machine ────────────────────────────────────────────────────


class GoalStatus(str, Enum):
    """Lifecycle of a goal. Stored as the literal string in ``goals.status``."""

    DRAFTING = "drafting"
    AWAITING_USER_INFO = "awaiting_user_info"
    EXECUTING = "executing"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    ABORTED = "aborted"


# Allowed (from, to) transitions. Anything not in this set is rejected
# in :func:`_assert_transition`. Terminal states have no outgoing edges.
_VALID_TRANSITIONS: frozenset[tuple[GoalStatus, GoalStatus]] = frozenset({
    (GoalStatus.DRAFTING, GoalStatus.AWAITING_USER_INFO),
    (GoalStatus.DRAFTING, GoalStatus.EXECUTING),
    (GoalStatus.DRAFTING, GoalStatus.FAILED),
    (GoalStatus.DRAFTING, GoalStatus.ABORTED),
    (GoalStatus.AWAITING_USER_INFO, GoalStatus.EXECUTING),
    (GoalStatus.AWAITING_USER_INFO, GoalStatus.AWAITING_USER_INFO),  # partial answers
    (GoalStatus.AWAITING_USER_INFO, GoalStatus.ABORTED),
    (GoalStatus.EXECUTING, GoalStatus.DONE),
    (GoalStatus.EXECUTING, GoalStatus.BLOCKED),
    (GoalStatus.EXECUTING, GoalStatus.FAILED),
    (GoalStatus.EXECUTING, GoalStatus.ABORTED),
    (GoalStatus.BLOCKED, GoalStatus.EXECUTING),
    (GoalStatus.BLOCKED, GoalStatus.AWAITING_USER_INFO),
    (GoalStatus.BLOCKED, GoalStatus.ABORTED),
})

_TERMINAL_STATES: frozenset[GoalStatus] = frozenset({
    GoalStatus.DONE, GoalStatus.FAILED, GoalStatus.ABORTED,
})


class InvalidGoalTransition(ValueError):
    """Raised when a state-machine edge isn't in :data:`_VALID_TRANSITIONS`."""


def _assert_transition(old: GoalStatus, new: GoalStatus) -> None:
    if old == new:
        return  # idempotent — re-applying same status is fine
    if (old, new) not in _VALID_TRANSITIONS:
        raise InvalidGoalTransition(
            f"cannot transition {old.value!r} → {new.value!r}"
        )


# ── Data model ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class GoalStep:
    """One executable step in a goal plan.

    Status is ``pending`` until the dispatch loop touches it; ``running``
    while in flight; ``done`` / ``failed`` once the specialist returns.
    Steps are stored as part of the encrypted ``plan_json`` blob — the
    table itself only carries denormalized counters.
    """
    idx: int
    description: str
    tool_hint: str | None = None
    status: str = "pending"
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "idx": self.idx,
            "description": self.description,
            "tool_hint": self.tool_hint,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GoalStep":
        return cls(
            idx=int(data.get("idx", 0)),
            description=str(data.get("description", "")),
            tool_hint=data.get("tool_hint"),
            status=str(data.get("status") or "pending"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
        )


@dataclass(frozen=True)
class Goal:
    """Full hydrated goal — plan + status + answers.

    Immutable: mutations go through :meth:`replace_with` to keep
    accidental in-place writes from leaking past serialization.
    """
    id: str
    user_id: str
    title: str
    status: GoalStatus
    plan: tuple[GoalStep, ...] = ()
    questions_pending: tuple[str, ...] = ()
    answers: dict[str, str] = field(default_factory=dict)
    risks: tuple[str, ...] = ()
    confidence: str = "medium"
    summary: str = ""
    # Structured classifier output set by the caller (e.g.
    # ``new_contract_intake`` stores ``web_monitoring`` /
    # ``data_scraping`` here). Read by dispatch executors that need
    # the type as a string — never inferred from ``summary`` because
    # the brain LLM's prose is not guaranteed to echo back the token.
    work_type: str | None = None
    blocked_on: str | None = None
    last_action: str = ""
    last_progress_at: float | None = None
    account_slug: str | None = None
    task_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def steps_total(self) -> int:
        return len(self.plan)

    @property
    def steps_done(self) -> int:
        return sum(1 for s in self.plan if s.status == "done")

    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATES


# ── Plan envelope JSON ───────────────────────────────────────────────


def _plan_envelope(goal: Goal) -> str:
    """Serialize the encrypted plan_json payload."""
    return json.dumps({
        "summary": goal.summary,
        "steps": [s.to_dict() for s in goal.plan],
        "questions_pending": list(goal.questions_pending),
        "answers": dict(goal.answers),
        "risks": list(goal.risks),
        "confidence": goal.confidence,
        "work_type": goal.work_type,
        "blocked_on": goal.blocked_on,
        "last_action": goal.last_action,
    })


def _plan_from_envelope(payload: str | None) -> dict:
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        logger.warning("goal_executor.plan_from_envelope failed to parse")
        return {}


# ── Repository ───────────────────────────────────────────────────────


_GOAL_COLUMNS = (
    "id", "user_id", "title", "status", "plan_json",
    "steps_total", "steps_done", "blocked_on", "last_action",
    "last_progress_at", "account_slug", "task_id",
    "created_at", "updated_at",
)


class GoalRepository:
    """Encrypted DAO over the ``goals`` table.

    All read paths return fully decrypted :class:`Goal` instances. All
    write paths take a ``Goal`` and persist it atomically (single
    INSERT or UPDATE — no read-modify-write between the two; callers
    are responsible for resolving optimistic conflicts via the
    ``updated_at`` column if they need to).
    """

    def __init__(self, config) -> None:
        self._config = config

    async def _key_aad(self, user_id: str, suffix: str) -> tuple[bytes, bytes]:
        key = await get_user_dek(self._config, user_id)
        return key, user_aad(user_id, suffix)

    async def _row_to_goal(self, row: tuple, user_id: str) -> Goal:
        key, title_aad = await self._key_aad(user_id, "goals:title")
        _, plan_aad = await self._key_aad(user_id, "goals:plan")
        (
            id_, _uid, enc_title, status_str, enc_plan,
            steps_total, steps_done, blocked_on, last_action,
            last_progress_at, account_slug, task_id,
            created_at, updated_at,
        ) = row

        title = decrypt_field(enc_title, key, title_aad) or ""
        plan_payload = decrypt_field(enc_plan, key, plan_aad)
        plan_data = _plan_from_envelope(plan_payload)

        steps = tuple(
            GoalStep.from_dict(s)
            for s in plan_data.get("steps") or []
        )
        questions = tuple(plan_data.get("questions_pending") or [])
        answers = dict(plan_data.get("answers") or {})
        risks = tuple(plan_data.get("risks") or [])
        confidence = str(plan_data.get("confidence") or "medium")
        summary = str(plan_data.get("summary") or "")
        # ``work_type`` may be absent on rows written before Phase 21 —
        # treat missing as None, never raise. Empty-string normalises
        # to None so downstream consumers can do a single ``is None``
        # check instead of distinguishing two falsy forms.
        wt_raw = plan_data.get("work_type")
        work_type = str(wt_raw).strip() if wt_raw else None
        if work_type == "":
            work_type = None

        try:
            status = GoalStatus(status_str)
        except ValueError:
            logger.warning("goal_executor unknown status %r in row %s", status_str, id_)
            status = GoalStatus.FAILED

        return Goal(
            id=id_, user_id=user_id, title=title, status=status,
            plan=steps, questions_pending=questions, answers=answers,
            risks=risks, confidence=confidence, summary=summary,
            work_type=work_type,
            blocked_on=blocked_on, last_action=last_action or "",
            last_progress_at=_iso_to_epoch(last_progress_at),
            account_slug=account_slug, task_id=task_id,
            created_at=created_at, updated_at=updated_at,
        )

    async def create(self, goal: Goal) -> Goal:
        """Insert a new row. Called once per goal at start time."""
        key, title_aad = await self._key_aad(goal.user_id, "goals:title")
        _, plan_aad = await self._key_aad(goal.user_id, "goals:plan")
        enc_title = encrypt_field(goal.title, key, title_aad)
        enc_plan = encrypt_field(_plan_envelope(goal), key, plan_aad)
        now = _utc_now_iso()
        last_progress_iso = (
            _epoch_to_iso(goal.last_progress_at) if goal.last_progress_at else None
        )
        async with db_session(self._config) as db:
            await db.execute(
                f"INSERT INTO goals ({', '.join(_GOAL_COLUMNS)}) VALUES "
                f"({', '.join(['?'] * len(_GOAL_COLUMNS))})",
                (
                    goal.id, goal.user_id, enc_title, goal.status.value, enc_plan,
                    goal.steps_total, goal.steps_done, goal.blocked_on,
                    goal.last_action, last_progress_iso,
                    goal.account_slug, goal.task_id, now, now,
                ),
            )
            await db.commit()
        logger.info(
            "goal_executor.create id=%s user=%s status=%s steps=%d",
            goal.id, goal.user_id, goal.status.value, goal.steps_total,
        )
        return replace(goal, created_at=now, updated_at=now)

    async def update(self, goal: Goal) -> Goal:
        """Replace persisted state with ``goal``. Touches ``updated_at``."""
        key, title_aad = await self._key_aad(goal.user_id, "goals:title")
        _, plan_aad = await self._key_aad(goal.user_id, "goals:plan")
        enc_title = encrypt_field(goal.title, key, title_aad)
        enc_plan = encrypt_field(_plan_envelope(goal), key, plan_aad)
        now = _utc_now_iso()
        last_progress_iso = (
            _epoch_to_iso(goal.last_progress_at) if goal.last_progress_at else None
        )
        async with db_session(self._config) as db:
            await db.execute(
                "UPDATE goals SET title=?, status=?, plan_json=?, "
                "steps_total=?, steps_done=?, blocked_on=?, last_action=?, "
                "last_progress_at=?, account_slug=?, task_id=?, updated_at=? "
                "WHERE id=? AND user_id=?",
                (
                    enc_title, goal.status.value, enc_plan,
                    goal.steps_total, goal.steps_done, goal.blocked_on,
                    goal.last_action, last_progress_iso,
                    goal.account_slug, goal.task_id, now,
                    goal.id, goal.user_id,
                ),
            )
            await db.commit()
        logger.debug(
            "goal_executor.update id=%s status=%s done=%d/%d",
            goal.id, goal.status.value, goal.steps_done, goal.steps_total,
        )
        return replace(goal, updated_at=now)

    async def get(self, user_id: str, goal_id: str) -> Goal | None:
        cols = ", ".join(_GOAL_COLUMNS)
        async with db_session(self._config) as db:
            row = await db.execute(
                f"SELECT {cols} FROM goals WHERE id=? AND user_id=?",
                (goal_id, user_id),
            )
            data = await row.fetchone()
        if data is None:
            return None
        return await self._row_to_goal(data, user_id)

    async def list(
        self,
        user_id: str,
        *,
        statuses: tuple[GoalStatus, ...] | None = None,
        limit: int = 50,
    ) -> list[Goal]:
        cols = ", ".join(_GOAL_COLUMNS)
        if statuses:
            placeholders = ", ".join(["?"] * len(statuses))
            sql = (
                f"SELECT {cols} FROM goals WHERE user_id=? "
                f"AND status IN ({placeholders}) "
                f"ORDER BY updated_at DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (
                user_id, *(s.value for s in statuses), limit,
            )
        else:
            sql = (
                f"SELECT {cols} FROM goals WHERE user_id=? "
                f"ORDER BY updated_at DESC LIMIT ?"
            )
            params = (user_id, limit)
        async with db_session(self._config) as db:
            row = await db.execute(sql, params)
            data = await row.fetchall()
        out: list[Goal] = []
        for r in data:
            out.append(await self._row_to_goal(r, user_id))
        return out


# ── Executor ─────────────────────────────────────────────────────────


# Type alias for the dispatch hook so tests can plug in a fake without
# importing the real AgentDispatcher (which pulls in the entire LLM
# stack and would make state-machine unit tests slow).
DispatchCallable = Callable[[Goal], Awaitable[None]]


# Per-account_slug default dispatch callbacks. Registered at import
# time by the relevant executor module (e.g.
# ``lazyclaw.runtime.contract_intake_executor`` registers itself for
# ``account_slug == "upwork"``). When a GoalExecutor is built WITHOUT
# an explicit ``dispatch_callback``, ``_dispatch`` falls back to the
# slug-specific entry from this registry. Lets goals reach EXECUTING
# and do real work even when the calling skill didn't pass a callback
# of its own.
_DEFAULT_DISPATCH_BY_SLUG: dict[str, DispatchCallable] = {}


def register_default_dispatch(
    account_slug: str, callback: DispatchCallable,
) -> None:
    """Register a default dispatch callback for goals with this slug.

    Called by executor modules at import time. Idempotent — re-
    registering replaces the previous entry. The slug is matched
    case-insensitively against ``Goal.account_slug``.
    """
    if not account_slug:
        raise ValueError("account_slug is required")
    _DEFAULT_DISPATCH_BY_SLUG[account_slug.strip().lower()] = callback


def _resolve_default_dispatch(
    account_slug: str | None,
) -> DispatchCallable | None:
    if not account_slug:
        return None
    return _DEFAULT_DISPATCH_BY_SLUG.get(account_slug.strip().lower())


class GoalExecutor:
    """Thin orchestrator on top of plan_research / fix_plan / dispatcher.

    Construction is intentionally permissive: pass ``dispatch_callback``
    to inject a custom executor (used in tests). Production wiring goes
    through :func:`make_default_dispatch` which wraps the live
    ``AgentDispatcher`` with browser-specialist tooling.
    """

    def __init__(
        self,
        config,
        repository: GoalRepository | None = None,
        *,
        dispatch_callback: DispatchCallable | None = None,
    ) -> None:
        self._config = config
        self._repo = repository or GoalRepository(config)
        self._dispatch_callback = dispatch_callback

    # ── Drafting ───────────────────────────────────────────────────

    async def start(
        self,
        user_id: str,
        title: str,
        *,
        hints: dict[str, str] | None = None,
        account_slug: str | None = None,
        work_type: str | None = None,
    ) -> Goal:
        """Draft a plan and persist a new Goal.

        Calls :func:`gather_plan_research` for free context, then
        :func:`build_fix_plan` for the structured plan. If the LLM
        couldn't produce a usable plan we still persist the goal in
        DRAFTING state so the user can retry; we never silently swallow
        the request.
        """
        if not title.strip():
            raise ValueError("title is required")

        # 1. Pull what LazyBrain already knows about this objective.
        try:
            from lazyclaw.lazybrain.embeddings import semantic_search
            lb_hits = await semantic_search(
                self._config, user_id, title, k=5, min_similarity=0.0,
            )
            lb_results = lb_hits.get("results") or []
        except Exception:
            logger.debug("goal_executor.start lazybrain lookup failed", exc_info=True)
            lb_results = []

        # 2. Gather zero-LLM context (4 parallel lanes).
        from lazyclaw.runtime.plan_research import gather_plan_research
        try:
            research_md = await gather_plan_research(
                self._config, user_id, title,
            )
        except Exception:
            logger.debug("goal_executor.start plan_research failed", exc_info=True)
            research_md = ""

        enriched_message = _build_enriched_message(title, hints, lb_results, research_md)

        # 3. Single brain LLM call to draft summary + steps + questions.
        from lazyclaw.runtime.fix_plan import build_fix_plan
        plan = None
        try:
            plan = await build_fix_plan(
                self._config, user_id, enriched_message, timeout_s=12.0,
            )
        except Exception:
            logger.warning("goal_executor.start build_fix_plan failed", exc_info=True)

        steps = tuple(
            GoalStep(idx=i, description=desc)
            for i, desc in enumerate(plan.steps if plan else [])
        )
        questions = tuple(plan.questions if plan else [])
        risks = tuple(plan.risks if plan else [])
        confidence = (plan.confidence if plan else "low") or "low"
        summary = (plan.summary if plan else title.strip()) or title.strip()

        if questions:
            initial_status = GoalStatus.AWAITING_USER_INFO
        elif steps:
            initial_status = GoalStatus.EXECUTING
        else:
            # No plan, no questions — keep the row visible in DRAFTING so
            # the user can retry. Failing here would lose the LLM-down
            # signal entirely.
            initial_status = GoalStatus.DRAFTING

        goal = Goal(
            id=_new_goal_id(),
            user_id=user_id,
            title=title.strip(),
            status=initial_status,
            plan=steps,
            questions_pending=questions,
            answers={},
            risks=risks,
            confidence=confidence,
            summary=summary,
            work_type=(work_type.strip() or None) if work_type else None,
            account_slug=account_slug,
            last_action="drafted plan" if plan else "draft failed — LLM unavailable",
            last_progress_at=time.time(),
        )
        goal = await self._repo.create(goal)

        if initial_status == GoalStatus.EXECUTING:
            await self._dispatch(goal)

        return goal

    # ── Answer + dispatch ──────────────────────────────────────────

    async def submit_answers(
        self,
        user_id: str,
        goal_id: str,
        answers: dict[str, str],
    ) -> Goal:
        """Record answers; transition to EXECUTING when all questions covered.

        Partial answers stay in AWAITING_USER_INFO with the remaining
        questions intact — caller can re-render the batch-ask surface
        and prompt for the missing ones. ``answers`` is keyed by the
        question text (verbatim) for simplicity; the UI passes back the
        same strings it received.
        """
        goal = await self._repo.get(user_id, goal_id)
        if goal is None:
            raise LookupError(f"goal not found: {goal_id}")
        if goal.status not in {
            GoalStatus.AWAITING_USER_INFO, GoalStatus.BLOCKED,
        }:
            raise InvalidGoalTransition(
                f"cannot submit_answers in status {goal.status.value!r}"
            )

        merged = dict(goal.answers)
        merged.update({str(k): str(v) for k, v in answers.items() if v is not None})

        # Recompute pending list — anything still unanswered stays.
        remaining = tuple(q for q in goal.questions_pending if q not in merged)

        next_status: GoalStatus
        if remaining:
            next_status = GoalStatus.AWAITING_USER_INFO
        else:
            next_status = GoalStatus.EXECUTING

        _assert_transition(goal.status, next_status)
        updated = replace(
            goal,
            status=next_status,
            answers=merged,
            questions_pending=remaining,
            last_action=(
                f"answered {len(merged)}/{len(goal.questions_pending) + len(merged) - len(merged)}"
                if not remaining
                else f"answered {len(answers)}; {len(remaining)} still pending"
            ),
            last_progress_at=time.time(),
        )
        updated = await self._repo.update(updated)
        if next_status == GoalStatus.EXECUTING:
            await self._dispatch(updated)
        return updated

    async def _dispatch(self, goal: Goal) -> None:
        """Hand off to a dispatch callback.

        Resolution order:
          1. Per-instance ``self._dispatch_callback`` (test injection or
             a skill that builds an executor with a custom callback).
          2. Per-slug default from ``_DEFAULT_DISPATCH_BY_SLUG`` —
             registered at import time by executor modules.
          3. None — log and pause at EXECUTING.
        """
        callback = self._dispatch_callback or _resolve_default_dispatch(
            goal.account_slug,
        )
        if callback is None:
            logger.info(
                "goal_executor.dispatch user=%s id=%s — no dispatch_callback "
                "configured (state machine paused at EXECUTING)",
                goal.user_id, goal.id,
            )
            return
        try:
            await callback(goal)
        except Exception as exc:
            logger.exception(
                "goal_executor.dispatch failed user=%s id=%s",
                goal.user_id, goal.id,
            )
            await self._fail(goal, f"dispatch error: {exc!s}")

    # ── Outcome handlers (called by dispatch wrappers) ─────────────

    async def mark_step(
        self,
        user_id: str,
        goal_id: str,
        idx: int,
        status: str,
        *,
        action: str = "",
        error: str | None = None,
    ) -> Goal:
        """Update one step's status and bump the goal counters."""
        goal = await self._repo.get(user_id, goal_id)
        if goal is None:
            raise LookupError(f"goal not found: {goal_id}")
        new_steps: list[GoalStep] = list(goal.plan)
        if not 0 <= idx < len(new_steps):
            raise IndexError(f"step idx {idx} out of range")
        existing = new_steps[idx]
        now = time.time()
        new_steps[idx] = replace(
            existing,
            status=status,
            started_at=existing.started_at or (now if status == "running" else None),
            completed_at=now if status in {"done", "failed"} else None,
            error=error,
        )
        updated = replace(
            goal,
            plan=tuple(new_steps),
            last_action=action or f"step {idx} {status}",
            last_progress_at=now,
        )
        return await self._repo.update(updated)

    async def mark_done(self, user_id: str, goal_id: str) -> Goal:
        goal = await self._repo.get(user_id, goal_id)
        if goal is None:
            raise LookupError(f"goal not found: {goal_id}")
        _assert_transition(goal.status, GoalStatus.DONE)
        return await self._repo.update(replace(
            goal,
            status=GoalStatus.DONE,
            last_action="done",
            last_progress_at=time.time(),
            blocked_on=None,
        ))

    async def mark_blocked(
        self, user_id: str, goal_id: str, reason: str,
    ) -> Goal:
        goal = await self._repo.get(user_id, goal_id)
        if goal is None:
            raise LookupError(f"goal not found: {goal_id}")
        _assert_transition(goal.status, GoalStatus.BLOCKED)
        return await self._repo.update(replace(
            goal,
            status=GoalStatus.BLOCKED,
            blocked_on=reason or "unspecified",
            last_action=f"blocked: {reason or 'unspecified'}",
            last_progress_at=time.time(),
        ))

    async def _fail(self, goal: Goal, reason: str) -> Goal:
        _assert_transition(goal.status, GoalStatus.FAILED)
        return await self._repo.update(replace(
            goal,
            status=GoalStatus.FAILED,
            last_action=f"failed: {reason}",
            last_progress_at=time.time(),
        ))

    async def abort(self, user_id: str, goal_id: str) -> Goal:
        """User-triggered abort. Terminal."""
        goal = await self._repo.get(user_id, goal_id)
        if goal is None:
            raise LookupError(f"goal not found: {goal_id}")
        if goal.is_terminal():
            return goal  # already terminal, no-op
        _assert_transition(goal.status, GoalStatus.ABORTED)
        return await self._repo.update(replace(
            goal,
            status=GoalStatus.ABORTED,
            last_action="aborted by user",
            last_progress_at=time.time(),
        ))

    # ── Status rendering (no LLM) ───────────────────────────────────

    async def status_block(
        self,
        user_id: str,
        goal_id: str | None = None,
    ) -> str:
        """Render a Markdown progress card.

        With ``goal_id``, renders one goal in detail (steps + last_action).
        Without it, renders a digest of all non-terminal goals — the form
        a user-wired ``[GOAL_PROGRESS]`` cron sees.
        """
        if goal_id is not None:
            goal = await self._repo.get(user_id, goal_id)
            if goal is None:
                return f"_Goal {goal_id} not found._"
            return _format_one(goal)

        active = await self._repo.list(
            user_id,
            statuses=(
                GoalStatus.DRAFTING, GoalStatus.AWAITING_USER_INFO,
                GoalStatus.EXECUTING, GoalStatus.BLOCKED,
            ),
        )
        if not active:
            return "_No active goals._"
        parts = ["**Active goals**", ""]
        for g in active:
            parts.append(_format_one_short(g))
        return "\n".join(parts)


# ── Helpers ──────────────────────────────────────────────────────────


def _new_goal_id() -> str:
    """Stable goal ID — uuid4 hex, 32 chars."""
    return uuid.uuid4().hex


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _epoch_to_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def _iso_to_epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _build_enriched_message(
    title: str,
    hints: dict[str, str] | None,
    lb_results: list[dict],
    research_md: str,
) -> str:
    """Compose the brain-LLM input that fix_plan.build_fix_plan sees.

    Front-loads what we already know about the user's business (LazyBrain
    semantic search) + plan_research's 4-lane context so the brain can
    skip questions whose answers are obvious from memory.
    """
    parts: list[str] = [title.strip()]
    if hints:
        bullet_lines = [f"- {k}: {v}" for k, v in hints.items() if v]
        if bullet_lines:
            parts.append("")
            parts.append("Hints from caller:")
            parts.extend(bullet_lines)
    if lb_results:
        parts.append("")
        parts.append("What LazyBrain already remembers about this:")
        for hit in lb_results[:5]:
            title_hit = str(hit.get("title") or "(untitled)")
            snippet = str(hit.get("snippet") or hit.get("body") or "")[:200]
            parts.append(f"- [[{title_hit}]] — {snippet}")
    if research_md:
        parts.append("")
        parts.append(research_md)
    parts.append("")
    parts.append(
        "Goal-executor mode: this is a multi-step objective the agent will run "
        "autonomously. Bias `steps` toward concrete, executable browser actions. "
        "Use `questions` ONLY for information that genuinely cannot be inferred "
        "from the LazyBrain context above (e.g. credentials, account choice, "
        "branding decisions). Empty questions[] is preferred when memory covers it."
    )
    return "\n".join(parts)


def _format_one(goal: Goal) -> str:
    pct = (
        f"{goal.steps_done}/{goal.steps_total}"
        if goal.steps_total else "0/0"
    )
    lines = [
        f"**Goal:** {goal.title}",
        f"**Status:** `{goal.status.value}` · steps {pct}",
    ]
    if goal.account_slug:
        lines.append(f"**Account:** `{goal.account_slug}`")
    if goal.summary:
        lines.append("")
        lines.append(goal.summary)
    if goal.plan:
        lines.append("")
        lines.append("**Steps:**")
        for s in goal.plan:
            mark = {
                "done": "✓", "failed": "✗", "running": "·",
                "pending": "○",
            }.get(s.status, "?")
            lines.append(f"  {mark} {s.idx + 1}. {s.description}")
    if goal.questions_pending:
        lines.append("")
        lines.append("**Pending questions:**")
        for q in goal.questions_pending:
            lines.append(f"  • {q}")
    if goal.blocked_on:
        lines.append("")
        lines.append(f"**Blocked on:** {goal.blocked_on}")
    if goal.last_action:
        lines.append("")
        lines.append(f"_Last: {goal.last_action}_")
    return "\n".join(lines)


def _format_one_short(goal: Goal) -> str:
    pct = (
        f"{goal.steps_done}/{goal.steps_total}"
        if goal.steps_total else "0/0"
    )
    suffix_parts: list[str] = []
    if goal.blocked_on:
        suffix_parts.append(f"blocked: {goal.blocked_on}")
    if goal.last_action:
        suffix_parts.append(goal.last_action)
    suffix = f" — {' · '.join(suffix_parts)}" if suffix_parts else ""
    return f"- `{goal.id[:8]}` **{goal.title}** [{goal.status.value}] {pct}{suffix}"
