"""Primary session resolver — shared history bucket across channels.

Every user has exactly one `agent_chat_sessions` row flagged `is_primary=1`.
Telegram, CLI, TUI, and REPL all tag their messages with that session id so
history is shared across channels. Web UI also opens it by default but can
branch off into non-primary sessions via "New Chat" for isolation.

The uniqueness constraint is enforced by
`idx_chat_sessions_primary` (partial unique index in schema.sql).
"""

from __future__ import annotations

import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# Per-user cache. Cleared by `invalidate_primary_session(user_id)` when the
# row is deleted or re-flagged. Small (one entry per active user) so no eviction.
_PRIMARY_CACHE: dict[str, str] = {}


async def get_primary_session_id(config: Config, user_id: str) -> str:
    """Return the user's primary chat_session_id, creating one if missing.

    Safe to call repeatedly — cached after first resolve. Under concurrent
    creation the partial unique index makes the second INSERT fail; we
    re-SELECT and return the winner.
    """
    cached = _PRIMARY_CACHE.get(user_id)
    if cached:
        return cached

    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions "
            "WHERE user_id = ? AND is_primary = 1 "
            "LIMIT 1",
            (user_id,),
        )
        existing = await row.fetchone()
        if existing:
            session_id = existing[0]
            _PRIMARY_CACHE[user_id] = session_id
            return session_id

        # No primary yet — promote the oldest session if one exists,
        # otherwise create a new "Main" session.
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions "
            "WHERE user_id = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (user_id,),
        )
        oldest = await row.fetchone()
        if oldest:
            session_id = oldest[0]
            try:
                await db.execute(
                    "UPDATE agent_chat_sessions SET is_primary = 1 "
                    "WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                )
                await db.commit()
            except Exception:
                logger.debug(
                    "Promote-to-primary failed — racing writer probably won",
                    exc_info=True,
                )
                # Fall through to re-SELECT below.
        else:
            session_id = str(uuid4())
            try:
                await db.execute(
                    "INSERT INTO agent_chat_sessions (id, user_id, title, is_primary) "
                    "VALUES (?, ?, ?, 1)",
                    (session_id, user_id, "Main"),
                )
                await db.commit()
            except Exception:
                logger.debug(
                    "Create-primary INSERT failed — racing writer probably won",
                    exc_info=True,
                )

        # Re-SELECT to absorb any race: under the partial unique index, the
        # loser's write never committed, so whoever's id is live wins.
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions "
            "WHERE user_id = ? AND is_primary = 1 "
            "LIMIT 1",
            (user_id,),
        )
        final = await row.fetchone()
        if not final:
            # Extreme edge case: partial index prevented both writes. Fall back
            # to the id we tried to create and hope the next call succeeds.
            logger.warning(
                "No primary session found after create attempt for user %s", user_id,
            )
            _PRIMARY_CACHE[user_id] = session_id
            return session_id

        session_id = final[0]
        _PRIMARY_CACHE[user_id] = session_id
        return session_id


def invalidate_primary_session(user_id: str) -> None:
    """Drop cached primary id for a user (call after delete or reflag)."""
    _PRIMARY_CACHE.pop(user_id, None)


def clear_cache() -> None:
    """Clear the entire cache — test-only helper."""
    _PRIMARY_CACHE.clear()
