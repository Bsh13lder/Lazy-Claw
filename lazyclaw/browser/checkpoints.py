"""Checkpoint approval — pause the agent and ask the user to confirm.

The agent calls `request_checkpoint(user_id, name)` before destructive or
high-stakes actions ("Submit booking", "Send payment", "Delete account").
The call BLOCKS until the user approves or rejects through the web UI
(/api/browser/checkpoint/...) or Telegram callback.

Approved checkpoint names are remembered per-user so the agent can re-call
them in a tight loop without re-prompting (e.g. "I already said yes once").

This module talks to event_bus so the canvas/Telegram light up immediately.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Default time to wait for an answer before treating it as a soft reject.
DEFAULT_TIMEOUT_SECONDS = 600  # 10 minutes


@dataclass
class _PendingCheckpoint:
    name: str
    detail: Optional[str]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: Optional[dict] = None     # {"approved": bool, "reason": str | None}
    created_at: float = field(default_factory=time.time)


# Per-user state — small in-memory dict; tasks are typically one-at-a-time per user.
_pending: dict[str, _PendingCheckpoint] = {}
_approved: dict[str, set[str]] = {}   # user_id -> {checkpoint_name, ...}


def has_approved(user_id: str, name: str) -> bool:
    return name in _approved.get(user_id, set())


def remember_approved(user_id: str, name: str) -> None:
    _approved.setdefault(user_id, set()).add(name)


def clear_approved(user_id: str) -> None:
    _approved.pop(user_id, None)


def get_pending(user_id: str) -> Optional[_PendingCheckpoint]:
    return _pending.get(user_id)


async def request_checkpoint(
    user_id: str,
    name: str,
    detail: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Block until the user approves or rejects this checkpoint.

    Returns: {"approved": bool, "reason": str | None}
    Auto-approves if the same checkpoint name was already approved in this
    session. Soft-rejects on timeout so the agent can move on / retry.
    """
    if not user_id or not name:
        return {"approved": False, "reason": "missing user or checkpoint name"}

    if has_approved(user_id, name):
        return {"approved": True, "reason": "auto-approved (previously confirmed)"}

    # Replace any older pending checkpoint for this user (only one at a time).
    existing = _pending.pop(user_id, None)
    if existing and not existing.event.is_set():
        existing.decision = {"approved": False, "reason": "superseded"}
        existing.event.set()

    pending = _PendingCheckpoint(name=name, detail=detail)
    _pending[user_id] = pending

    # Tell the UI / Telegram. Lazy import so checkpoints.py stays importable
    # without the rest of the browser package being loaded.
    try:
        from lazyclaw.browser import event_bus
        event_bus.publish(event_bus.BrowserEvent(
            user_id=user_id,
            kind="checkpoint",
            target=name,
            detail=detail or f"Approve to continue: {name}",
            extra={"name": name},
        ))
    except Exception:
        logger.debug("Checkpoint event publish failed (non-fatal)", exc_info=True)

    try:
        await asyncio.wait_for(pending.event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pending.decision = {"approved": False, "reason": "timed out waiting for user"}
    finally:
        # Drop the pending entry so a later checkpoint can take its slot.
        if _pending.get(user_id) is pending:
            _pending.pop(user_id, None)

    decision = pending.decision or {"approved": False, "reason": "unknown"}
    if decision.get("approved"):
        remember_approved(user_id, name)
    return decision


def approve(user_id: str, name: str | None = None, reason: str | None = None) -> bool:
    """Approve the pending checkpoint for this user. Returns True if released."""
    pending = _pending.get(user_id)
    if pending is None or pending.event.is_set():
        return False
    if name is not None and pending.name != name:
        # Name mismatch — refuse to approve a different checkpoint than the user saw.
        logger.warning(
            "Checkpoint approve name mismatch for %s: got %r, pending %r",
            user_id, name, pending.name,
        )
        return False
    pending.decision = {"approved": True, "reason": reason}
    pending.event.set()
    return True


def reject(user_id: str, name: str | None = None, reason: str | None = None) -> bool:
    """Reject the pending checkpoint. Returns True if released."""
    pending = _pending.get(user_id)
    if pending is None or pending.event.is_set():
        return False
    if name is not None and pending.name != name:
        logger.warning(
            "Checkpoint reject name mismatch for %s: got %r, pending %r",
            user_id, name, pending.name,
        )
        return False
    pending.decision = {"approved": False, "reason": reason or "rejected by user"}
    pending.event.set()
    return True
