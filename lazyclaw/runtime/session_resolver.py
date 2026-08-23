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

# Reserved title for the per-user BACKGROUND session — the isolated bucket that
# cron / heartbeat / watcher turns run in, kept OUT of the primary interactive
# session. Without this, every background job shared the primary's context, so
# one ad-hoc interactive turn (e.g. a content-policy refusal on a one-off task)
# poisoned every recurring monitor's context on its next run (2026-08-23:
# a himap "scan endpoints" refusal in the primary session started getting the
# unrelated Upwork-inbox watch refused too). Never surfaced as a normal chat.
BACKGROUND_SESSION_TITLE = "__background_jobs__"

_BACKGROUND_CACHE: dict[str, str] = {}


async def get_primary_session_id(config: Config, user_id: str) -> str:
    """Return the user's primary chat_session_id, creating one if missing.

    Safe to call repeatedly — cached after first resolve. Under concurrent
    creation the partial unique index makes the second INSERT fail; we
    re-SELECT and return the winner.
    """
    cached = _PRIMARY_CACHE.get(user_id)
    if cached:
        logger.debug(
            "[session] primary session cache hit user=%s session=%s",
            user_id[:8] if user_id else user_id, cached,
        )
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
            logger.debug(
                "[session] resolved existing primary session user=%s session=%s",
                user_id[:8] if user_id else user_id, session_id,
            )
            return session_id

        # No primary yet — promote the oldest session if one exists,
        # otherwise create a new "Main" session. NEVER promote the reserved
        # background session: a cron job must not hijack the shared bucket.
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions "
            "WHERE user_id = ? AND title != ? "
            "ORDER BY created_at ASC LIMIT 1",
            (user_id, BACKGROUND_SESSION_TITLE),
        )
        oldest = await row.fetchone()
        if oldest:
            session_id = oldest[0]
            logger.debug(
                "[session] promoting oldest session to primary user=%s session=%s",
                user_id[:8] if user_id else user_id, session_id,
            )
            try:
                await db.execute(
                    "UPDATE agent_chat_sessions SET is_primary = 1 "
                    "WHERE id = ? AND user_id = ?",
                    (session_id, user_id),
                )
                await db.commit()
            except Exception:
                logger.debug(
                    "[session] promote-to-primary failed for user=%s — "
                    "racing writer probably won",
                    user_id[:8] if user_id else user_id,
                    exc_info=True,
                )
                # Fall through to re-SELECT below.
        else:
            session_id = str(uuid4())
            logger.debug(
                "[session] creating new primary session user=%s session=%s",
                user_id[:8] if user_id else user_id, session_id,
            )
            try:
                await db.execute(
                    "INSERT INTO agent_chat_sessions (id, user_id, title, is_primary) "
                    "VALUES (?, ?, ?, 1)",
                    (session_id, user_id, "Main"),
                )
                await db.commit()
            except Exception:
                logger.debug(
                    "[session] create-primary INSERT failed for user=%s — "
                    "racing writer probably won",
                    user_id[:8] if user_id else user_id,
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
                "[session] no primary session found after create attempt for user=%s "
                "(falling back to attempted session=%s)",
                user_id[:8] if user_id else user_id, session_id,
            )
            _PRIMARY_CACHE[user_id] = session_id
            return session_id

        session_id = final[0]
        _PRIMARY_CACHE[user_id] = session_id
        logger.debug(
            "[session] resolved primary session (post-race re-select) user=%s session=%s",
            user_id[:8] if user_id else user_id, session_id,
        )
        return session_id


async def get_background_session_id(config: Config, user_id: str) -> str:
    """Return the user's dedicated background/cron session id, creating it once.

    Heartbeat, watcher, and cron turns run here instead of the primary session
    so interactive content never bleeds into a recurring job's context (and vice
    versa). ``is_primary`` stays 0 so it never collides with the primary partial
    unique index, and the reserved title keeps it out of primary promotion.
    """
    cached = _BACKGROUND_CACHE.get(user_id)
    if cached:
        return cached

    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions "
            "WHERE user_id = ? AND title = ? AND is_primary = 0 "
            "ORDER BY created_at ASC LIMIT 1",
            (user_id, BACKGROUND_SESSION_TITLE),
        )
        existing = await row.fetchone()
        if existing:
            _BACKGROUND_CACHE[user_id] = existing[0]
            return existing[0]

        session_id = str(uuid4())
        try:
            await db.execute(
                "INSERT INTO agent_chat_sessions (id, user_id, title, is_primary) "
                "VALUES (?, ?, ?, 0)",
                (session_id, user_id, BACKGROUND_SESSION_TITLE),
            )
            await db.commit()
        except Exception:
            logger.debug(
                "[session] background-session INSERT lost a race for user=%s",
                user_id[:8] if user_id else user_id, exc_info=True,
            )

        # Re-SELECT the oldest matching row so concurrent creators converge.
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions "
            "WHERE user_id = ? AND title = ? AND is_primary = 0 "
            "ORDER BY created_at ASC LIMIT 1",
            (user_id, BACKGROUND_SESSION_TITLE),
        )
        final = await row.fetchone()
        session_id = final[0] if final else session_id
        _BACKGROUND_CACHE[user_id] = session_id
        return session_id


def invalidate_primary_session(user_id: str) -> None:
    """Drop cached primary id for a user (call after delete or reflag)."""
    had_cached = _PRIMARY_CACHE.pop(user_id, None) is not None
    logger.debug(
        "[session] invalidate_primary_session user=%s had_cached=%s",
        user_id[:8] if user_id else user_id, had_cached,
    )


def clear_cache() -> None:
    """Clear the entire cache — test-only helper."""
    _PRIMARY_CACHE.clear()
    _BACKGROUND_CACHE.clear()
