"""Unified inbox HTTP routes — Task C4.

Exposes channel-thread metadata and live message access over:

  GET  /api/inbox/threads                   — list threads (optionally filtered by channel)
  GET  /api/inbox/threads/changes           — delta feed for offline-sync clients
  GET  /api/inbox/threads/{id}/messages     — live messages via ChannelGateway
  GET  /api/inbox/threads/{id}/media/{mid}  — download/stream a message's media bytes
  POST /api/inbox/threads/{id}/read         — mark thread as read
  POST /api/inbox/threads/{id}/reply        — direct send OR start an AI conversation
  GET/PUT/DELETE /api/inbox/channels/{ch}/instruction — per-channel standing instruction

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
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
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

# Media downloads pull bytes from WhatsApp servers via the MCP — heavier than
# a read, so they get their own (more generous than replies) per-user budget.
_media_limiter = _RateLimiter(max_requests=30, window_seconds=60)

# Message ids come from channel reads (WhatsApp ids are URL-safe tokens);
# reject anything that could smuggle path segments into the MCP call.
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-=.]{1,128}$")

# Sane "type/subtype" (+ optional parameters) before echoing a tool-provided
# mimetype into a response header.
_MIME_RE = re.compile(r"^[\w.+-]+/[\w.+-]+(;\s*[\w.+-]+=[^;]+)*$")

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


class InstructionBody(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


class ContactNameBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


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
                # media metadata (voice note / photo / file) + the message id
                # needed to fetch the bytes via the /media/{message_id} route.
                "id": m.id,
                "media": m.media,
            }
            for m in msgs
        ],
        "thread": thread,
    }


@router.get("/threads/{thread_id}/media/{message_id}")
async def get_thread_media(
    thread_id: str,
    message_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Download the media behind one message and stream it to the client.

    The per-channel MCP downloads the bytes to its media dir and returns the
    file path; this route streams that file with the right content type so
    the mobile app can render a playable voice note / photo inline.
    """
    if not _MESSAGE_ID_RE.match(message_id):
        raise HTTPException(status_code=400, detail="invalid message id")
    if not _media_limiter.check(user.id):
        raise HTTPException(
            status_code=429, detail="Too many media downloads — wait a minute and retry."
        )

    thread = await thread_store.get_thread(config, user.id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    registry = _get_registry()
    gw = build_gateway(registry, user.id)
    result = await gw.download_media(
        thread["channel"], message_id, contact=thread["contact_handle"],
    )

    path = result.get("path") if isinstance(result, dict) else None
    if not path or result.get("error"):
        # Log the real error server-side; the client gets a generic message —
        # tool errors can carry internal paths (same policy as reply sends).
        logger.warning(
            "inbox media download failed (thread=%s message=%s): %s",
            thread_id, message_id,
            result.get("error") if isinstance(result, dict) else result,
        )
        raise HTTPException(status_code=502, detail="media download failed")

    media_file = Path(path)
    if not media_file.is_file():
        logger.warning(
            "inbox media download returned a missing file (thread=%s): %s",
            thread_id, path,
        )
        raise HTTPException(status_code=502, detail="media download failed")

    # Sanitize the mimetype before echoing it into a response header.
    mimetype = str(result.get("mimetype") or "application/octet-stream")
    if not _MIME_RE.match(mimetype):
        mimetype = "application/octet-stream"

    return FileResponse(
        media_file,
        media_type=mimetype,
        filename=str(result.get("file_name") or media_file.name),
    )


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
        # NOTE: call signature is the SHARED Phase E contract — keyword-args
        # (channel, contact, goal) only, so the fuller conversation-runner
        # state machine on feat/comms-continue drops in without route changes.
        try:
            conv = await conversation_runner.start(
                config, user.id,
                channel=thread["channel"],
                contact=thread["contact_handle"],
                goal=body.text,
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="AI replies are not supported on this channel.",
            )
        except RuntimeError:
            raise HTTPException(
                status_code=503,
                detail="AI conversation mode needs the agent runtime — try again shortly.",
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


# ── Per-thread (per-contact) standing instruction + contact naming ────────────


@router.put("/threads/{thread_id}/instruction")
async def put_thread_instruction(
    thread_id: str,
    body: InstructionBody,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Set the standing instruction for THIS contact — the agent executes it
    as a real turn whenever they write (on top of the channel-wide one)."""
    ok = await thread_store.set_thread_instruction(
        config, user.id, thread_id, body.instruction,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"success": True, "thread_id": thread_id}


@router.delete("/threads/{thread_id}/instruction")
async def delete_thread_instruction(
    thread_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Clear the per-contact standing instruction."""
    ok = await thread_store.set_thread_instruction(config, user.id, thread_id, None)
    return {"success": ok}


@router.put("/threads/{thread_id}/contact")
async def put_thread_contact_name(
    thread_id: str,
    body: ContactNameBody,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Name the thread's contact — updates the thread, attaches the handle to
    the unified contact store, and creates a [[Name]] LazyBrain page."""
    thread = await thread_store.get_thread(config, user.id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    from lazyclaw.comms import contact_naming

    try:
        result = await contact_naming.name_thread_contact(
            config, user.id, thread, body.name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, **result}


# ── Per-channel standing instructions ──────────────────────────────────────────


def _validated_channel(channel: str) -> str:
    if not _CHANNEL_PARAM_RE.match(channel):
        raise HTTPException(status_code=400, detail="invalid channel")
    return channel


@router.get("/channels/{channel}/instruction")
async def get_channel_instruction(
    channel: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Read the standing instruction for a channel (or null when unset)."""
    from lazyclaw.comms import channel_instructions

    try:
        result = await channel_instructions.get_channel_instruction(
            config, user.id, _validated_channel(channel),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result or {"channel": channel, "instruction": None}


@router.put("/channels/{channel}/instruction")
async def put_channel_instruction(
    channel: str,
    body: InstructionBody,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Set the standing instruction — executed as a real agent turn whenever
    new messages arrive on the channel (result pushed via notifications)."""
    from lazyclaw.comms import channel_instructions

    try:
        result = await channel_instructions.set_channel_instruction(
            config, user.id, _validated_channel(channel), body.instruction,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, **result}


@router.delete("/channels/{channel}/instruction")
async def delete_channel_instruction(
    channel: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Clear the standing instruction (the watcher keeps notifying)."""
    from lazyclaw.comms import channel_instructions

    try:
        removed = await channel_instructions.clear_channel_instruction(
            config, user.id, _validated_channel(channel),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": removed}
