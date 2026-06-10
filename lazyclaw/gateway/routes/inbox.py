"""Unified inbox HTTP routes — Task C4.

Exposes channel-thread metadata and live message access over:

  GET  /api/inbox/threads                   — list threads (optionally filtered by channel)
  GET  /api/inbox/threads/changes           — delta feed for offline-sync clients
  GET  /api/inbox/threads/{id}/messages     — live messages via ChannelGateway
  POST /api/inbox/threads/{id}/read         — mark thread as read
  POST /api/inbox/threads/{id}/reply        — direct send OR start an AI conversation

Registry access: module-level ``_shared_registry`` singleton, identical to the
pattern used in ``gateway/routes/chat_ws.py`` (lines 29–42). The singleton is
populated by ``app.py:set_inbox_registry`` at startup (same as chat_ws receives
its registry via ``set_chat_ws_deps``). When not set, falls back to a bare
SkillRegistry so the route is always importable / testable without a live runtime.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lazyclaw.config import Config, load_config
from lazyclaw.comms import thread_store
from lazyclaw.comms.gateway import build_gateway
from lazyclaw.gateway.auth import User, _RateLimiter, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

# Channel filter must be a sane lowercase token (matches thread_store's rule) —
# rejects path-traversal junk without hardcoding a closed channel list.
_CHANNEL_PARAM_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# Replies trigger live external channel sends (WhatsApp/email/Instagram) —
# rate-limit per user like the vault routes do (auth.py _RateLimiter pattern).
_reply_limiter = _RateLimiter(max_requests=20, window_seconds=60)

# Injected by app.py at startup (same module-level-singleton pattern as chat_ws.py).
_shared_registry = None


def set_inbox_registry(registry) -> None:
    """Called by app.py to inject the shared SkillRegistry (with MCP tools)."""
    global _shared_registry
    _shared_registry = registry


def _get_registry():
    """Return the shared registry, falling back to a bare SkillRegistry if not set."""
    if _shared_registry is not None:
        return _shared_registry
    logger.warning(
        "inbox: using bare SkillRegistry fallback — set_inbox_registry() was not called;"
        " channel sends will fail"
    )
    from lazyclaw.skills.registry import SkillRegistry
    reg = SkillRegistry()
    return reg


# ── Pydantic models ────────────────────────────────────────────────────────────


class ReplyBody(BaseModel):
    # 4096 chars ≈ the push.py max_chars=3800 budget with headroom; multi-MB
    # bodies are rejected at the schema boundary before any processing.
    text: str = Field(min_length=1, max_length=4096)
    mode: Literal["direct", "ai"] = "direct"


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/threads")
async def list_threads(
    channel: str | None = Query(None, description="Filter to a single channel, e.g. 'whatsapp'"),
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """List live (non-deleted) threads for the authenticated user.

    Returns ``{"threads": [...], "count": n}``.
    """
    if channel is not None and not _CHANNEL_PARAM_RE.match(channel):
        raise HTTPException(status_code=400, detail="invalid channel")
    threads = await thread_store.list_threads(config, user.id, channel=channel)
    return {"threads": threads, "count": len(threads)}


@router.get("/threads/changes")
async def get_thread_changes(
    since: str | None = Query(
        None,
        description=(
            "ISO-8601 timestamp — return threads updated after this value. "
            "Use the ``now`` field from the previous response as the next ``since`` value. "
            "Omit for a full pull."
        ),
    ),
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Delta feed for offline-sync clients.

    Returns ``{threads, deleted, now}``.
    """
    if since is not None:
        # An unencoded '+' in a query string URL-decodes to a space, mangling
        # ISO offsets like '+00:00'. Repair that case (a space after the 'T'
        # separator can only be a mangled '+') before validating.
        if "T" in since and " " in since:
            since = since.replace(" ", "+")
        # SQLite compares timestamps as strings — a malformed value would
        # silently return a wrong result set instead of erroring.
        try:
            datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid 'since' timestamp")
    return await thread_store.get_thread_changes(config, user.id, since)


@router.get("/threads/{thread_id}/messages")
async def get_thread_messages(
    thread_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Return live messages for a thread via ChannelGateway.

    Returns ``{"messages": [...], "thread": {...}}``.
    """
    thread = await thread_store.get_thread(config, user.id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    registry = _get_registry()
    gw = build_gateway(registry, user.id)
    msgs = await gw.read_thread(thread["channel"], thread["contact_handle"])
    return {
        "messages": [
            {
                "sender": m.sender,
                "text": m.text,
                "timestamp": m.timestamp,
                "is_mine": m.is_mine,
            }
            for m in msgs
        ],
        "thread": thread,
    }


@router.post("/threads/{thread_id}/read")
async def mark_thread_read(
    thread_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Zero the unread count for a thread.

    Returns ``{"success": true}`` when the thread was found and updated,
    ``{"success": false}`` when no matching row exists.
    """
    ok = await thread_store.mark_thread_read(config, user.id, thread_id)
    return {"success": ok}


@router.post("/threads/{thread_id}/reply")
async def reply_to_thread(
    thread_id: str,
    body: ReplyBody,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Send a reply to a thread.

    ``mode="direct"`` (default): sends immediately via ChannelGateway.
    ``mode="ai"``: starts an AI conversation (Phase E — lazy-imported; returns 503
    if ``lazyclaw.comms.conversation_runner`` is not yet available).

    Returns ``{"success": true, "mode": "<mode>"}`` (direct) or
    ``{"success": true, "conversation_id": "<id>", "mode": "ai"}`` (ai).
    """
    if not _reply_limiter.check(user.id):
        raise HTTPException(
            status_code=429, detail="Too many sends — wait a minute and retry."
        )

    thread = await thread_store.get_thread(config, user.id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    if body.mode == "ai":
        # Lazy import — conversation_runner won't exist until Phase E.
        try:
            from lazyclaw.comms import conversation_runner  # noqa: PLC0415
        except ImportError:
            raise HTTPException(
                status_code=503,
                detail=(
                    "AI conversation mode is not available yet "
                    "(conversation_runner not installed — Phase E)."
                ),
            )
        conv = await conversation_runner.start(
            config, user.id,
            channel=thread["channel"],
            contact=thread["contact_handle"],
            goal=body.text,
        )
        return {"success": True, "conversation_id": conv["id"], "mode": "ai"}

    # Default: direct send via ChannelGateway.
    registry = _get_registry()
    gw = build_gateway(registry, user.id)
    res = await gw.send(thread["channel"], thread["contact_handle"], body.text)
    if not res.ok:
        # Log the real error server-side; the client gets a generic message —
        # raw exception strings can carry internal paths/endpoints/payloads.
        logger.warning(
            "inbox reply send failed (thread=%s channel=%s): %s",
            thread_id, thread["channel"], res.error,
        )
        raise HTTPException(status_code=502, detail="send failed")
    return {"success": True, "mode": "direct"}
