"""Chat History API — session CRUD and message retrieval."""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.crypto.encryption import decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.runtime.session_resolver import (
    get_primary_session_id,
    invalidate_primary_session,
)

logger = logging.getLogger(__name__)

_config = load_config()

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _extract_tool_calls(metadata_raw: str | None) -> list | None:
    """Parse stored message metadata into a tool-call list, fail-soft.

    Two shapes exist on disk: the 2026-06-10 metadata-encryption pass
    stores the tool-call list directly (``[...]``); older rows wrap it
    as ``{"tool_calls": [...]}``. Anything unparseable (including the
    ``[encrypted]`` decrypt fallback sentinel) returns None — a bad row
    must never take down the whole history response.
    """
    if not metadata_raw:
        return None
    try:
        meta = json.loads(metadata_raw)
    except (json.JSONDecodeError, TypeError):
        logger.debug("Failed to parse message metadata JSON", exc_info=True)
        return None
    if isinstance(meta, list):
        return meta
    if isinstance(meta, dict):
        tool_calls = meta.get("tool_calls")
        return tool_calls if isinstance(tool_calls, list) else None
    return None


class CreateSessionRequest(BaseModel):
    title: str = Field(default="New Chat", max_length=200)


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    archived: bool | None = None


@router.get("/sessions")
async def list_sessions(user: User = Depends(get_current_user)):
    """List user's chat sessions (non-archived, newest first).

    Also repairs orphaned messages — creates missing session rows
    for any chat_session_id that has messages but no session entry —
    and ensures the user has a primary session (the shared bucket that
    Telegram / CLI / TUI / REPL all write into).
    """
    # Ensure the user has a primary session. First call on a fresh DB
    # promotes the oldest existing session or creates "Main".
    await get_primary_session_id(_config, user.id)

    async with db_session(_config) as db:
        # Repair orphaned messages — create missing session rows
        await db.execute(
            "INSERT OR IGNORE INTO agent_chat_sessions (id, user_id, message_count) "
            "SELECT m.chat_session_id, m.user_id, COUNT(*) "
            "FROM agent_messages m "
            "LEFT JOIN agent_chat_sessions s ON s.id = m.chat_session_id "
            "WHERE m.user_id = ? AND s.id IS NULL "
            "AND m.chat_session_id IS NOT NULL "
            "GROUP BY m.chat_session_id",
            (user.id,),
        )
        await db.commit()

        rows = await db.execute(
            "SELECT id, title, message_count, is_primary, created_at "
            "FROM agent_chat_sessions "
            "WHERE user_id = ? AND archived_at IS NULL "
            "ORDER BY is_primary DESC, created_at DESC",
            (user.id,),
        )
        sessions = [
            {
                "id": r[0],
                "title": r[1] or "New Chat",
                "message_count": r[2] or 0,
                "is_primary": bool(r[3]),
                "created_at": r[4],
            }
            for r in await rows.fetchall()
        ]
    return {"sessions": sessions}


@router.post("/sessions")
async def create_session(
    body: CreateSessionRequest,
    user: User = Depends(get_current_user),
):
    """Create a new chat session."""
    logger.debug(
        "[route:chat] POST session user=%s fields=%s",
        user.id, list(body.model_dump(exclude_unset=True).keys()),
    )
    session_id = str(uuid4())
    async with db_session(_config) as db:
        await db.execute(
            "INSERT INTO agent_chat_sessions (id, user_id, title) VALUES (?, ?, ?)",
            (session_id, user.id, body.title),
        )
        await db.commit()
    return {"id": session_id, "title": body.title}


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    user: User = Depends(get_current_user),
):
    """Rename or archive a chat session.

    Archiving the primary session is blocked: it would hide the shared
    cross-channel history from the Web UI while Telegram / CLI keep writing
    into it — a confusing silent failure.
    """
    logger.debug(
        "[route:chat] PATCH session id=%s user=%s fields=%s",
        session_id, user.id, list(body.model_dump(exclude_unset=True).keys()),
    )
    async with db_session(_config) as db:
        row = await db.execute(
            "SELECT id, is_primary FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        found = await row.fetchone()
        if not found:
            logger.warning(
                "[route:chat] PATCH session id=%s user=%s -> 404 session not found",
                session_id, user.id,
            )
            raise HTTPException(status_code=404, detail="Session not found")

        if body.archived is True and found[1]:
            logger.warning(
                "[route:chat] PATCH session id=%s user=%s -> 409 cannot archive primary session",
                session_id, user.id,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot archive the primary session — it's the shared "
                    "history bucket for Telegram, CLI, TUI, and REPL."
                ),
            )

        if body.title is not None:
            await db.execute(
                "UPDATE agent_chat_sessions SET title = ? WHERE id = ? AND user_id = ?",
                (body.title, session_id, user.id),
            )
        if body.archived is True:
            await db.execute(
                "UPDATE agent_chat_sessions SET archived_at = datetime('now') WHERE id = ? AND user_id = ?",
                (session_id, user.id),
            )
        await db.commit()
    return {"status": "updated"}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
):
    """Delete a chat session and all its messages.

    The primary session (shared across channels) cannot be deleted. Empty it
    with PATCH { archived: true } or clear the messages via the agent's
    /clear flow instead — deleting it would orphan Telegram/CLI history.
    """
    async with db_session(_config) as db:
        row = await db.execute(
            "SELECT id, is_primary FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        found = await row.fetchone()
        if not found:
            logger.warning(
                "[route:chat] DELETE session id=%s user=%s -> 404 session not found",
                session_id, user.id,
            )
            raise HTTPException(status_code=404, detail="Session not found")

        if found[1]:  # is_primary = 1
            logger.warning(
                "[route:chat] DELETE session id=%s user=%s -> 409 cannot delete primary session",
                session_id, user.id,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete the primary session — it's the shared "
                    "history bucket for Telegram, CLI, TUI, and REPL. "
                    "Archive or clear its messages instead."
                ),
            )

        await db.execute(
            "DELETE FROM agent_messages WHERE chat_session_id = ? AND user_id = ?",
            (session_id, user.id),
        )
        await db.execute(
            "DELETE FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        await db.commit()

    invalidate_primary_session(user.id)
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: int = 50,
    before: str | None = None,
    user: User = Depends(get_current_user),
):
    """Load decrypted messages for a chat session (paginated).

    Clients seed their chat screens from this endpoint, so the default page
    is the NEWEST `limit` messages; `before=<message_id>` pages upward
    through older history. Both pages are returned oldest-first, ready to
    render top-to-bottom.

    Turns are batch-persisted with one shared created_at (agent.py
    post-loop), so ordering and the `before` anchor use (created_at, rowid)
    — created_at alone would skip or reshuffle same-timestamp siblings.
    """
    key = await get_user_dek(_config, user.id)

    async with db_session(_config) as db:
        # Verify session belongs to user
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        if not await row.fetchone():
            logger.warning(
                "[route:chat] GET session messages id=%s user=%s -> 404 session not found",
                session_id, user.id,
            )
            raise HTTPException(status_code=404, detail="Session not found")

        if before:
            rows = await db.execute(
                "SELECT id, role, content, tool_name, metadata, created_at "
                "FROM agent_messages "
                "WHERE user_id = ? AND chat_session_id = ? "
                "AND (created_at, rowid) < "
                "(SELECT created_at, rowid FROM agent_messages WHERE id = ?) "
                "ORDER BY created_at DESC, rowid DESC "
                "LIMIT ?",
                (user.id, session_id, before, limit),
            )
        else:
            rows = await db.execute(
                "SELECT id, role, content, tool_name, metadata, created_at "
                "FROM agent_messages "
                "WHERE user_id = ? AND chat_session_id = ? "
                "ORDER BY created_at DESC, rowid DESC "
                "LIMIT ?",
                (user.id, session_id, limit),
            )

        messages = []
        for r in reversed(list(await rows.fetchall())):
            content = decrypt_field(r[2], key) or ""
            metadata_raw = decrypt_field(r[4], key) if r[4] else None

            tool_calls = _extract_tool_calls(metadata_raw)

            messages.append({
                "id": r[0],
                "role": r[1],
                "content": content,
                "tool_name": r[3],
                "tool_calls": tool_calls,
                "created_at": r[5],
            })

    logger.debug(
        "[route:chat] GET session messages id=%s user=%s limit=%d paged=%s -> count=%d",
        session_id, user.id, limit, bool(before), len(messages),
    )
    return {"messages": messages}
