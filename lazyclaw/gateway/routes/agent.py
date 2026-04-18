"""Agent API routes — user-facing plan approval gate."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from lazyclaw.browser import event_bus
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.runtime import plan_checkpoint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


# ── Plan approval gate ──────────────────────────────────────────────────


@router.get("/plan")
async def plan_pending(user: User = Depends(get_current_user)):
    """Return the pending plan awaiting approval, if any."""
    pending = plan_checkpoint.get_pending(user.id)
    if pending is None:
        return {"pending": None}
    return {
        "pending": {
            "plan": pending.plan_text,
            "steps": pending.steps,
            "created_at": pending.created_at,
        },
        "auto_approve_session": plan_checkpoint.is_session_auto_approved(user.id),
    }


@router.post("/plan/approve")
async def plan_approve(
    user: User = Depends(get_current_user),
    payload: dict = Body(default={}),
):
    """Approve the pending plan, releasing the agent.

    Payload:
      - reason (optional): free text
      - auto_approve_session (optional, bool): trust the agent for 30 min
    """
    payload = payload or {}
    reason = payload.get("reason")
    auto = bool(payload.get("auto_approve_session", False))

    released = plan_checkpoint.approve(
        user.id, reason=reason, auto_approve_session=auto,
    )
    if not released:
        raise HTTPException(
            status_code=409, detail="No plan awaiting approval",
        )
    event_bus.publish(event_bus.BrowserEvent(
        user_id=user.id,
        kind="plan",
        target="plan",
        detail="Plan approved",
        extra={
            "status": "approved",
            "auto_approve_session": auto,
        },
    ))
    return {"status": "approved", "auto_approve_session": auto}


@router.post("/plan/reject")
async def plan_reject(
    user: User = Depends(get_current_user),
    payload: dict = Body(default={}),
):
    """Reject the pending plan with an optional reason."""
    payload = payload or {}
    reason = (payload.get("reason") or "rejected by user").strip()
    released = plan_checkpoint.reject(user.id, reason=reason)
    if not released:
        raise HTTPException(
            status_code=409, detail="No plan awaiting approval",
        )
    event_bus.publish(event_bus.BrowserEvent(
        user_id=user.id,
        kind="plan",
        target="plan",
        detail=f"Plan rejected: {reason}",
        extra={"status": "rejected", "reason": reason},
    ))
    return {"status": "rejected", "reason": reason}


# ── Plan mode user settings ─────────────────────────────────────────────


@router.get("/plan/settings")
async def plan_settings(user: User = Depends(get_current_user)):
    """Return the user's auto_plan preference + session trust state."""
    from lazyclaw.runtime.agent import _load_auto_plan_setting
    auto_plan = await _load_auto_plan_setting(user.id)
    return {
        "auto_plan": auto_plan,
        "session_auto_approve": plan_checkpoint.is_session_auto_approved(user.id),
    }


@router.post("/plan/settings")
async def set_plan_settings(
    user: User = Depends(get_current_user),
    payload: dict = Body(default={}),
):
    """Toggle the user's auto_plan preference (plan mode ON/OFF)."""
    payload = payload or {}
    if "auto_plan" in payload:
        from lazyclaw.config import load_config
        from lazyclaw.db.connection import db_session
        value = 1 if payload.get("auto_plan") else 0
        config = load_config()
        try:
            async with db_session(config) as conn:
                await conn.execute(
                    "UPDATE users SET auto_plan = ? WHERE id = ?",
                    (value, user.id),
                )
                await conn.commit()
        except Exception as exc:
            logger.error("Failed to update auto_plan: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
    if payload.get("clear_session_trust"):
        plan_checkpoint.clear_session_auto_approve(user.id)
    return await plan_settings(user=user)
