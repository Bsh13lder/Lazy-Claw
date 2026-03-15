"""Replay engine — load and render trace sessions as timelines."""

from __future__ import annotations

import json
import logging

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, decrypt
from lazyclaw.db.connection import db_session
from lazyclaw.replay.models import TraceEntry, TraceSession

logger = logging.getLogger(__name__)


async def list_traces(
    config: Config, user_id: str, limit: int = 20
) -> list[TraceSession]:
    """List recent trace sessions for a user."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT trace_session_id, "
            "COUNT(*) as entry_count, "
            "MIN(created_at) as started_at, "
            "MAX(created_at) as ended_at, "
            "GROUP_CONCAT(DISTINCT entry_type) as types "
            "FROM agent_traces "
            "WHERE user_id = ? "
            "GROUP BY trace_session_id "
            "ORDER BY MAX(created_at) DESC "
            "LIMIT ?",
            (user_id, limit),
        )
        all_rows = await rows.fetchall()

    return [
        TraceSession(
            trace_session_id=row[0],
            user_id=user_id,
            entry_count=row[1],
            started_at=row[2],
            ended_at=row[3],
            entry_types=tuple(row[4].split(",")) if row[4] else (),
        )
        for row in all_rows
    ]


async def get_trace(
    config: Config, user_id: str, trace_session_id: str
) -> list[TraceEntry]:
    """Load all entries for a trace session, decrypted."""
    key = derive_server_key(config.server_secret, user_id)

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, trace_session_id, sequence, entry_type, "
            "content, metadata, created_at "
            "FROM agent_traces "
            "WHERE user_id = ? AND trace_session_id = ? "
            "ORDER BY sequence ASC",
            (user_id, trace_session_id),
        )
        all_rows = await rows.fetchall()

    entries = []
    for row in all_rows:
        entry_id, sess_id, seq, etype, content_enc, meta_json, created = row
        content = decrypt(content_enc, key) if content_enc.startswith("enc:") else content_enc
        metadata = json.loads(meta_json) if meta_json else None

        entries.append(TraceEntry(
            id=entry_id,
            trace_session_id=sess_id,
            sequence=seq,
            entry_type=etype,
            content=content,
            metadata=metadata,
            created_at=created,
        ))

    return entries


async def get_trace_by_token(
    config: Config, share_token: str
) -> tuple[list[TraceEntry], str] | None:
    """Load a trace via share token. Returns (entries, user_id) or None."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT user_id, trace_session_id, expires_at "
            "FROM trace_shares WHERE share_token = ?",
            (share_token,),
        )
        share = await row.fetchone()

    if not share:
        return None

    user_id, trace_session_id, expires_at = share

    # Check expiration
    if expires_at:
        from datetime import datetime
        try:
            exp = datetime.fromisoformat(expires_at)
            if datetime.utcnow() > exp:
                return None
        except ValueError:
            pass

    entries = await get_trace(config, user_id, trace_session_id)
    return entries, user_id


async def delete_trace(
    config: Config, user_id: str, trace_session_id: str
) -> int:
    """Delete all entries for a trace session. Returns count deleted."""
    async with db_session(config) as db:
        # Delete shares first
        await db.execute(
            "DELETE FROM trace_shares WHERE user_id = ? AND trace_session_id = ?",
            (user_id, trace_session_id),
        )
        # Delete trace entries
        result = await db.execute(
            "DELETE FROM agent_traces WHERE user_id = ? AND trace_session_id = ?",
            (user_id, trace_session_id),
        )
        await db.commit()
        return result.rowcount
