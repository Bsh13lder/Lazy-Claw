"""Heartbeat-driven state machine for autonomous channel conversations.

States: drafting -> awaiting_approval -> running -> done | failed | expired | aborted
step() advances ONE conversation by one move per heartbeat tick (restart-safe).
Conversation dicts (from conversation_store) carry 'user_id'.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from lazyclaw.config import Config
from lazyclaw.comms import conversation_store as cs
from lazyclaw.comms.gateway import build_gateway
from lazyclaw.notifications.dispatch import deliver

logger = logging.getLogger(__name__)


class RunnerDeps(Protocol):
    """Structural protocol for runtime dependencies injected into step/draft/run.

    SimpleNamespace satisfies this at runtime (structural typing — no changes needed
    in tests or callers).
    """

    registry: object
    eco_router: object
    permission_checker: object


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def start(
    config: Config,
    user_id: str,
    *,
    channel: str,
    contact: str,
    goal: str,
    completion_criteria: str | None = None,
    max_iterations: int = 20,
    poll_interval: int = 60,
    ttl_hours: int = 24,
) -> dict:
    """Create a new conversation task and schedule it due immediately.

    Returns the conversation dict with status='drafting' and next_poll_at set
    so the next heartbeat tick will pick it up for drafting.
    """
    expires = _iso(_now() + timedelta(hours=ttl_hours))
    conv = await cs.create_conversation(
        config,
        user_id,
        channel=channel,
        contact_handle=contact,
        contact_name=None,
        goal=goal,
        completion_criteria=completion_criteria,
        max_iterations=max_iterations,
        poll_interval=poll_interval,
        expires_at=expires,
    )
    # Schedule due immediately so the next heartbeat drafts the opener.
    scheduled = await cs.update_conversation(
        config, user_id, conv["id"], next_poll_at=_iso(_now())
    )
    if scheduled is None:
        raise RuntimeError("conversation vanished during start scheduling")
    return scheduled


async def step(config: Config, deps: RunnerDeps, conv: dict) -> dict:
    """Advance one conversation by one move.

    deps carries registry/eco_router/permission_checker for specialist runs.
    conv carries user_id (as returned by conversation_store).
    """
    user_id = conv["user_id"]

    if conv.get("expires_at") and conv["expires_at"] < _iso(_now()):
        return await _fail(config, conv, "expired", error="timed out")

    status = conv["status"]

    if status == "drafting":
        draft = await _draft_message(config, deps, conv)
        if not draft:
            return await _fail(config, conv, "failed", error="could not draft opener")
        approval_id = await _request_approval(config, user_id, conv, draft)
        return await cs.update_conversation(
            config,
            user_id,
            conv["id"],
            status="awaiting_approval",
            approval_id=approval_id,
            next_poll_at=None,
            append_transcript={"dir": "draft", "text": draft, "ts": _iso(_now())},
        )

    if status == "running":
        return await _run_step(config, deps, conv)  # implemented in E5

    return conv


async def _fail(config: Config, conv: dict, status: str, *, error: str) -> dict:
    """Transition a conversation to a terminal failure state and notify the user."""
    user_id = conv["user_id"]
    updated = await cs.update_conversation(
        config, user_id, conv["id"], status=status, error=error, next_poll_at=None
    )
    await deliver(
        config,
        user_id,
        title="Conversation ended",
        body=f"Couldn't finish asking {conv['contact_handle']}: {error}",
        kind="conversation_result",
        thread_ref={"channel": conv["channel"], "contact": conv["contact_handle"]},
    )
    return updated


async def _draft_message(config: Config, deps: RunnerDeps, conv: dict) -> str | None:
    """Ask the messaging specialist to draft the opening message from the goal.

    Uses run_specialist (lazyclaw/teams/runner.py:210) with the messaging_specialist
    config loaded from BUILTIN_SPECIALISTS (lazyclaw/teams/specialist.py:111) via
    _BUILTIN_BY_NAME["messaging_specialist"] (lazyclaw/teams/specialist.py:113).

    Returns the draft text on success, or None if the specialist/registry is
    unavailable or the run fails.
    """
    if deps.registry is None or deps.eco_router is None:
        logger.debug("_draft_message: specialist deps unavailable — returning None")
        return None

    try:
        from lazyclaw.teams.runner import run_specialist
        from lazyclaw.teams.specialist import _BUILTIN_BY_NAME
    except ImportError:
        logger.warning("_draft_message: teams.runner or teams.specialist not importable")
        return None

    messaging_spec = _BUILTIN_BY_NAME.get("messaging_specialist")
    if messaging_spec is None:
        logger.warning("_draft_message: messaging_specialist not in BUILTIN_SPECIALISTS")
        return None

    task = (
        f"Draft a short, friendly opening message to send on {conv['channel']} "
        f"to {conv['contact_handle']} with the goal: {conv['goal']}. "
        f"Return ONLY the message text — no explanation, no quotes, no preamble."
    )

    try:
        result = await run_specialist(
            user_id=conv["user_id"],
            specialist=messaging_spec,
            task=task,
            registry=deps.registry,
            eco_router=deps.eco_router,
            permission_checker=deps.permission_checker,
        )
    except Exception:
        logger.exception("_draft_message: run_specialist raised")
        return None

    if not result.success:
        logger.warning("_draft_message: specialist failed — %s", result.error)
        return None

    return result.result.strip() or None


async def _request_approval(
    config: Config, user_id: str, conv: dict, draft: str
) -> str:
    """Emit a first-message approval request and return its approval id."""
    from lazyclaw.comms import approvals

    return await approvals.request_first_message_approval(config, user_id, conv, draft)


async def _run_step(config: Config, deps: RunnerDeps, conv: dict) -> dict:
    """Poll/reply branch — implemented in Task E5."""
    return conv


async def on_approval(config: Config, deps: RunnerDeps, conv: dict, approved: bool) -> dict:
    """Handle a user approve/deny decision for a first-message approval.

    On approve: sends the drafted opener via ChannelGateway, transitions status
    to 'running', schedules the first poll.
    On deny: aborts the conversation.
    """
    user_id = conv["user_id"]
    if not approved:
        return await _fail(config, conv, "aborted", error="you cancelled the first message")
    draft = next((t["text"] for t in reversed(conv["transcript"]) if t.get("dir") == "draft"), None)
    gw = build_gateway(deps.registry, user_id)
    res = await gw.send(conv["channel"], conv["contact_handle"], draft or "")
    if not res.ok:
        return await _fail(config, conv, "failed", error=res.error or "send failed")
    return await cs.update_conversation(
        config, user_id, conv["id"], status="running", approval_id=None,
        next_poll_at=_iso(_now() + timedelta(seconds=conv["poll_interval"])),
        append_transcript={"dir": "out", "text": draft, "ts": _iso(_now())})
