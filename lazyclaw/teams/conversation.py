"""Team conversation storage — encrypted internal agent messages.

Stores instructions from team lead to specialists, specialist results,
and critic reviews in the agent_team_messages table. All content is
encrypted at rest using the user's server-derived key.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamMessage:
    """Immutable team conversation message."""

    id: str
    team_session_id: str
    from_agent: str
    to_agent: str
    message_type: str  # instruction, result, critique
    content: str
    created_at: str


async def store_message(
    config: Config,
    user_id: str,
    team_session_id: str,
    from_agent: str,
    to_agent: str,
    message_type: str,
    content: str,
) -> str:
    """Store an encrypted team message. Returns the message ID."""
    key = await get_user_dek(config, user_id)
    msg_id = str(uuid4())
    encrypted_content = encrypt(content, key)

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO agent_team_messages "
            "(id, user_id, team_session_id, from_agent, to_agent, "
            "message_type, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, user_id, team_session_id, from_agent, to_agent,
             message_type, encrypted_content),
        )
        await db.commit()

    return msg_id


async def get_session(
    config: Config, user_id: str, team_session_id: str
) -> list[TeamMessage]:
    """Get all messages in a team session, decrypted."""
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, team_session_id, from_agent, to_agent, "
            "message_type, content, created_at "
            "FROM agent_team_messages "
            "WHERE user_id = ? AND team_session_id = ? "
            "ORDER BY created_at ASC",
            (user_id, team_session_id),
        )
        all_rows = await rows.fetchall()

    messages = []
    for row in all_rows:
        msg_id, sess_id, from_a, to_a, msg_type, content_enc, created = row
        content = decrypt_field(content_enc, key)
        messages.append(TeamMessage(
            id=msg_id,
            team_session_id=sess_id,
            from_agent=from_a,
            to_agent=to_a,
            message_type=msg_type,
            content=content,
            created_at=created,
        ))

    return messages


async def list_sessions(
    config: Config, user_id: str, limit: int = 20
) -> list[dict]:
    """List team sessions with basic metadata (no content decryption)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT team_session_id, "
            "COUNT(*) as message_count, "
            "MIN(created_at) as started_at, "
            "MAX(created_at) as ended_at "
            "FROM agent_team_messages "
            "WHERE user_id = ? "
            "GROUP BY team_session_id "
            "ORDER BY MAX(created_at) DESC "
            "LIMIT ?",
            (user_id, limit),
        )
        all_rows = await rows.fetchall()

    return [
        {
            "team_session_id": row[0],
            "message_count": row[1],
            "started_at": row[2],
            "ended_at": row[3],
        }
        for row in all_rows
    ]


async def cleanup_old(config: Config, days: int = 30) -> int:
    """Delete team messages older than the given number of days. Returns count deleted."""
    async with db_session(config) as db:
        result = await db.execute(
            "DELETE FROM agent_team_messages "
            "WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await db.commit()
        return result.rowcount
