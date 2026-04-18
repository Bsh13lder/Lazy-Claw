"""Plan approval — show the user the agent's plan and wait for go/no-go.

Modeled after `lazyclaw/browser/checkpoints.py` but plan-scoped so browser
checkpoints and plan checkpoints don't collide.

Flow:
  1. Agent classifies a turn as MEDIUM+ effort and generates a plan.
  2. Agent calls `request_plan_approval(user_id, plan_text, steps)` — BLOCKS.
  3. Web UI / Telegram show the plan with Approve / Reject / Auto-approve buttons.
  4. User decision releases the event; the agent continues or aborts.

Three auto-approve levels:
  * **Per-turn phrase bypass** — handled upstream (agent checks message for
    "just do it" / "go ahead" / "hecho" before calling this module).
  * **Session auto-approve** — `set_session_auto_approve(user_id, ttl=300)`
    auto-approves every plan for N seconds without blocking.
  * **Global `auto_plan=False`** — user setting skips plan mode entirely
    (also handled upstream).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 600  # 10 minutes — match browser checkpoints


@dataclass
class PlanDecision:
    approved: bool
    reason: Optional[str] = None
    auto_approve_session: bool = False   # User ticked "don't ask again this session"


@dataclass
class PendingPlan:
    plan_text: str
    steps: list[str]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: Optional[PlanDecision] = None
    created_at: float = field(default_factory=time.time)
    # Clarification mode — set when the model returned `QUESTION: ...`
    # instead of a plan. `question` is what to ask the user; `answer` is
    # what they typed back. When both are set the waiter resumes.
    question: Optional[str] = None
    answer: Optional[str] = None


@dataclass
class _PendingClarification:
    question: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    answer: Optional[str] = None
    created_at: float = field(default_factory=time.time)


# Per-user state. Only one plan pending at a time per user.
_pending: dict[str, PendingPlan] = {}

# Per-user clarification (question) state. One at a time per user.
_pending_q: dict[str, _PendingClarification] = {}

# Session-level auto-approve. user_id -> expiry_timestamp.
_auto_approve_until: dict[str, float] = {}


def is_session_auto_approved(user_id: str) -> bool:
    expiry = _auto_approve_until.get(user_id)
    if expiry is None:
        return False
    if time.time() >= expiry:
        _auto_approve_until.pop(user_id, None)
        return False
    return True


def set_session_auto_approve(user_id: str, ttl_seconds: int = 300) -> None:
    """Trust the agent for ttl_seconds — next plans auto-approve.

    Default shortened to 5 min (was 30). Longer windows let the agent
    pivot into tool-loops silently when a task hits a wall; 5 min still
    covers a normal multi-step task but forces a re-review for new asks.
    """
    _auto_approve_until[user_id] = time.time() + ttl_seconds
    logger.info(
        "Plan auto-approve enabled for user %s for %ds", user_id, ttl_seconds,
    )


def clear_session_auto_approve(user_id: str) -> None:
    _auto_approve_until.pop(user_id, None)


def get_pending(user_id: str) -> Optional[PendingPlan]:
    return _pending.get(user_id)


async def request_plan_approval(
    user_id: str,
    plan_text: str,
    steps: list[str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> PlanDecision:
    """Show the plan to the user and block until they decide.

    Returns a PlanDecision with approved/reason. Soft-rejects on timeout.
    """
    if not user_id or not plan_text:
        return PlanDecision(False, "missing user or plan")

    # Session auto-approve — skip the blocking round-trip entirely.
    if is_session_auto_approved(user_id):
        logger.info("Plan auto-approved for user %s (session trust)", user_id)
        _publish_plan_event(
            user_id, plan_text, steps, status="auto_approved",
        )
        return PlanDecision(True, "auto-approved (session trust)")

    # Replace any stale pending plan for this user.
    existing = _pending.pop(user_id, None)
    if existing and not existing.event.is_set():
        existing.decision = PlanDecision(False, "superseded by newer plan")
        existing.event.set()

    pending = PendingPlan(plan_text=plan_text, steps=list(steps))
    _pending[user_id] = pending

    _publish_plan_event(user_id, plan_text, steps, status="pending")

    try:
        await asyncio.wait_for(pending.event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pending.decision = PlanDecision(
            False, "timed out waiting for approval",
        )
    finally:
        if _pending.get(user_id) is pending:
            _pending.pop(user_id, None)

    decision = pending.decision or PlanDecision(False, "unknown")
    if decision.approved and decision.auto_approve_session:
        set_session_auto_approve(user_id)
    return decision


def get_pending_question(user_id: str) -> Optional[_PendingClarification]:
    return _pending_q.get(user_id)


async def request_clarification(
    user_id: str,
    question: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[str]:
    """Ask the user a clarifying question and wait for their typed answer.

    Returns the answer text, or None on timeout / cancellation.
    """
    if not user_id or not question:
        return None

    existing = _pending_q.pop(user_id, None)
    if existing and not existing.event.is_set():
        existing.answer = None
        existing.event.set()

    pending = _PendingClarification(question=question.strip())
    _pending_q[user_id] = pending

    _publish_question_event(user_id, question)

    try:
        await asyncio.wait_for(pending.event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pending.answer = None
    finally:
        if _pending_q.get(user_id) is pending:
            _pending_q.pop(user_id, None)

    return pending.answer


def answer_clarification(user_id: str, answer: str) -> bool:
    """Release the pending question with the user's typed answer."""
    pending = _pending_q.get(user_id)
    if pending is None or pending.event.is_set():
        return False
    pending.answer = (answer or "").strip() or None
    pending.event.set()
    return True


def cancel_clarification(user_id: str) -> bool:
    pending = _pending_q.get(user_id)
    if pending is None or pending.event.is_set():
        return False
    pending.answer = None
    pending.event.set()
    return True


def _publish_question_event(user_id: str, question: str) -> None:
    try:
        from lazyclaw.browser import event_bus
        event_bus.publish(event_bus.BrowserEvent(
            user_id=user_id,
            kind="plan_question",
            target="plan_question",
            detail=question[:300],
            extra={"question": question},
        ))
    except Exception:
        logger.debug("Plan question publish failed (non-fatal)", exc_info=True)


def approve(
    user_id: str,
    reason: Optional[str] = None,
    auto_approve_session: bool = False,
) -> bool:
    """Release the pending plan with approval. Returns True if released."""
    pending = _pending.get(user_id)
    if pending is None or pending.event.is_set():
        return False
    pending.decision = PlanDecision(
        True, reason, auto_approve_session=auto_approve_session,
    )
    pending.event.set()
    return True


def reject(user_id: str, reason: Optional[str] = None) -> bool:
    """Release the pending plan with rejection."""
    pending = _pending.get(user_id)
    if pending is None or pending.event.is_set():
        return False
    pending.decision = PlanDecision(False, reason or "rejected by user")
    pending.event.set()
    return True


def _publish_plan_event(
    user_id: str,
    plan_text: str,
    steps: list[str],
    status: str,
) -> None:
    """Notify UI / Telegram via the browser event bus.

    Reuses BrowserEvent with kind="plan" so existing WebSocket / Telegram
    pumps forward it without new plumbing. Extra carries the full plan.
    """
    try:
        from lazyclaw.browser import event_bus
        event_bus.publish(event_bus.BrowserEvent(
            user_id=user_id,
            kind="plan",
            target="plan",
            detail=plan_text[:300],
            extra={
                "status": status,
                "plan": plan_text,
                "steps": steps,
            },
        ))
    except Exception:
        logger.debug("Plan event publish failed (non-fatal)", exc_info=True)
