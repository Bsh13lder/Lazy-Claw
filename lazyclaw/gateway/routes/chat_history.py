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

logger = logging.getLogger(__name__)

_config = load_config()

router = APIRouter(prefix="/api/chat", tags=["chat"])


class CreateSessionRequest(BaseModel):
    title: str = Field(default="New Chat", max_length=200)


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    archived: bool | None = None


@router.get("/sessions")
async def list_sessions(user: User = Depends(get_current_user)):
    """List user's chat sessions (non-archived, newest first)."""
    async with db_session(_config) as db:
        rows = await db.execute(
            "SELECT id, title, message_count, created_at "
            "FROM agent_chat_sessions "
            "WHERE user_id = ? AND archived_at IS NULL "
            "ORDER BY created_at DESC",
            (user.id,),
        )
        sessions = [
            {
                "id": r[0],
                "title": r[1] or "New Chat",
                "message_count": r[2] or 0,
                "created_at": r[3],
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
    """Rename or archive a chat session."""
    async with db_session(_config) as db:
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        if not await row.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

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
    """Delete a chat session and all its messages."""
    async with db_session(_config) as db:
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        if not await row.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        await db.execute(
            "DELETE FROM agent_messages WHERE chat_session_id = ? AND user_id = ?",
            (session_id, user.id),
        )
        await db.execute(
            "DELETE FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        await db.commit()
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: int = 50,
    before: str | None = None,
    user: User = Depends(get_current_user),
):
    """Load decrypted messages for a chat session (paginated)."""
    key = await get_user_dek(_config, user.id)

    async with db_session(_config) as db:
        # Verify session belongs to user
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        if not await row.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        if before:
            rows = await db.execute(
                "SELECT id, role, content, tool_name, metadata, created_at "
                "FROM agent_messages "
                "WHERE user_id = ? AND chat_session_id = ? "
                "AND created_at < (SELECT created_at FROM agent_messages WHERE id = ?) "
                "ORDER BY created_at ASC "
                "LIMIT ?",
                (user.id, session_id, before, limit),
            )
        else:
            rows = await db.execute(
                "SELECT id, role, content, tool_name, metadata, created_at "
                "FROM agent_messages "
                "WHERE user_id = ? AND chat_session_id = ? "
                "ORDER BY created_at ASC "
                "LIMIT ?",
                (user.id, session_id, limit),
            )

        messages = []
        for r in await rows.fetchall():
            content = decrypt_field(r[2], key) or ""
            metadata_raw = decrypt_field(r[4], key) if r[4] else None

            tool_calls = None
            if metadata_raw:
                try:
                    meta = json.loads(metadata_raw)
                    tool_calls = meta.get("tool_calls")
                except (json.JSONDecodeError, TypeError):
                    pass

            messages.append({
                "id": r[0],
                "role": r[1],
                "content": content,
                "tool_name": r[3],
                "tool_calls": tool_calls,
                "created_at": r[5],
            })

    return {"messages": messages}
