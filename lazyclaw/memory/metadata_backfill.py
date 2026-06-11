"""One-time idempotent backfill: encrypt legacy plaintext tool metadata.

Historical ``agent_messages.metadata`` rows hold plaintext tool-call
arguments (credentials included). The tolerant read path in
``metadata_codec`` means the system works without this backfill — it
exists to remove the at-rest exposure, mirroring the typed-memory
backfill pattern (fire-and-forget at gateway startup, per-user DEK,
never raises).
"""

from __future__ import annotations

import logging

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.memory.metadata_codec import encode_tool_metadata

logger = logging.getLogger(__name__)


async def backfill_encrypt_tool_metadata(config: Config) -> int:
    """Encrypt every plaintext ``agent_messages.metadata`` row.

    Idempotent — encrypted rows (``enc:%``) are excluded by the query, so
    a second run is a no-op. Per-user failures are logged and skipped;
    returns the number of rows encrypted.
    """
    async with db_session(config) as db:
        rows = await (
            await db.execute(
                "SELECT DISTINCT user_id FROM agent_messages "
                "WHERE metadata IS NOT NULL AND metadata NOT LIKE 'enc:%'"
            )
        ).fetchall()
    user_ids = [r[0] for r in rows]
    if not user_ids:
        return 0

    total = 0
    for user_id in user_ids:
        try:
            total += await _backfill_user(config, user_id)
        except Exception:
            logger.warning(
                "tool-metadata backfill failed for user %s — skipping",
                user_id, exc_info=True,
            )
    if total:
        logger.info("tool-metadata backfill encrypted %d legacy rows", total)
    return total


async def _backfill_user(config: Config, user_id: str) -> int:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await (
            await db.execute(
                "SELECT id, metadata FROM agent_messages "
                "WHERE user_id = ? AND metadata IS NOT NULL "
                "AND metadata NOT LIKE 'enc:%'",
                (user_id,),
            )
        ).fetchall()
        updates = [
            (encoded, msg_id)
            for msg_id, plaintext in rows
            if (encoded := encode_tool_metadata(plaintext, key)) is not None
        ]
        if updates:
            await db.executemany(
                "UPDATE agent_messages SET metadata = ? WHERE id = ?",
                updates,
            )
            await db.commit()
    return len(updates)
