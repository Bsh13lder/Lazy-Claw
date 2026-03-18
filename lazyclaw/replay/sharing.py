"""Trace sharing — generate and manage shareable tokens for replays."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


async def create_share(
    config: Config,
    user_id: str,
    trace_session_id: str,
    expires_hours: int | None = 72,
) -> dict:
    """Create a shareable token for a trace session.

    Args:
        config: App config
        user_id: Owner of the trace
        trace_session_id: Trace to share
        expires_hours: Hours until token expires (None = never)

    Returns:
        Dict with share_id, token, expires_at
    """
    # Verify trace exists
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT COUNT(*) FROM agent_traces "
            "WHERE user_id = ? AND trace_session_id = ?",
            (user_id, trace_session_id),
        )
        count = (await row.fetchone())[0]

    if count == 0:
        raise ValueError("Trace session not found")

    share_id = str(uuid4())
    token = secrets.token_urlsafe(32)
    expires_at = None
    if expires_hours is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO trace_shares "
            "(id, user_id, trace_session_id, share_token, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (share_id, user_id, trace_session_id, token, expires_at),
        )
        await db.commit()

    logger.info("Created share token for trace %s", trace_session_id)
    return {
        "share_id": share_id,
        "token": token,
        "trace_session_id": trace_session_id,
        "expires_at": expires_at,
    }


async def revoke_share(config: Config, user_id: str, share_id: str) -> bool:
    """Revoke a share token. Returns True if deleted."""
    async with db_session(config) as db:
        result = await db.execute(
            "DELETE FROM trace_shares WHERE id = ? AND user_id = ?",
            (share_id, user_id),
        )
        await db.commit()
        return result.rowcount > 0


async def list_shares(
    config: Config, user_id: str, trace_session_id: str | None = None
) -> list[dict]:
    """List share tokens for a user or specific trace."""
    async with db_session(config) as db:
        if trace_session_id:
            rows = await db.execute(
                "SELECT id, trace_session_id, share_token, expires_at, created_at "
                "FROM trace_shares "
                "WHERE user_id = ? AND trace_session_id = ? "
                "ORDER BY created_at DESC",
                (user_id, trace_session_id),
            )
        else:
            rows = await db.execute(
                "SELECT id, trace_session_id, share_token, expires_at, created_at "
                "FROM trace_shares WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
        all_rows = await rows.fetchall()

    return [
        {
            "share_id": row[0],
            "trace_session_id": row[1],
            "token": row[2],
            "expires_at": row[3],
            "created_at": row[4],
        }
        for row in all_rows
    ]
