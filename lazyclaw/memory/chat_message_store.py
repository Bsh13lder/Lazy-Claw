"""Shared writer + marker helpers for notification rows in chat history.

``agent_messages`` historically had exactly ONE writer — the post-loop
persistence block in ``lazyclaw/runtime/agent.py`` (Agent.process_message).
The notification chat-card leg (``lazyclaw/notifications/chat_card.py``)
needs to append standalone assistant rows too, so the encryption + insert
shape lives here as a small reusable helper instead of being duplicated
inline. The turn writer in agent.py is untouched (its batch semantics —
title auto-set, multi-row executemany — stay turn-specific).

Marker contract: a notification row carries
``{"notification_card": true, "kind": "<kind>"}`` in the (encrypted)
``metadata`` column. Consumers:
  * ``memory/compressor.py`` EXCLUDES marked rows from the LLM context —
    a notification must never be re-read by the brain as something it
    previously said (documented history-pollution incident class);
  * ``memory/daily_log.py`` skips marked rows when summarizing a day;
  * ``gateway/routes/chat_history.py`` exposes ``"kind": "notification"``
    on marked rows so clients can style them.
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.memory.metadata_codec import encode_tool_metadata

logger = logging.getLogger(__name__)

# Plaintext key inside the (decrypted) metadata JSON that marks a row as a
# UI-only notification card.
NOTIFICATION_CARD_MARKER = "notification_card"


def build_notification_metadata(kind: str) -> str:
    """Serialize the notification-card marker metadata (plaintext JSON).

    The caller (or :func:`append_assistant_message`) encrypts it with the
    user DEK via :func:`lazyclaw.memory.metadata_codec.encode_tool_metadata`
    before storage — same envelope as tool-call metadata.
    """
    return json.dumps({
        NOTIFICATION_CARD_MARKER: True,
        "kind": (kind or "info").strip() or "info",
    })


def is_notification_card_metadata(metadata_plain: str | dict | None) -> bool:
    """True when decoded ``agent_messages.metadata`` carries the card marker.

    Accepts the plaintext JSON string returned by
    :func:`lazyclaw.memory.metadata_codec.decode_tool_metadata` (or an
    already-parsed dict). Tolerant by design: tool-call metadata is a JSON
    LIST, legacy rows may hold anything — everything non-matching returns
    False, never raises.
    """
    if not metadata_plain:
        return False
    if isinstance(metadata_plain, dict):
        meta: object = metadata_plain
    else:
        try:
            meta = json.loads(metadata_plain)
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
    return isinstance(meta, dict) and bool(meta.get(NOTIFICATION_CARD_MARKER))


async def append_assistant_message(
    config: Config,
    user_id: str,
    chat_session_id: str | None,
    content: str,
    *,
    metadata_json: str | None = None,
) -> str:
    """Append ONE encrypted assistant-role row to a chat session.

    Mirrors the agent.py turn writer byte-for-byte on the storage side:
    same column set, ``content`` encrypted with the user DEK, ``metadata``
    (when given, as plaintext JSON) encrypted via ``encode_tool_metadata``,
    session row upserted and ``message_count`` refreshed. Returns the new
    message id. Raises on failure — callers on the notification path wrap
    this (see ``notifications/chat_card.py``).
    """
    key = await get_user_dek(config, user_id)
    msg_id = str(uuid4())
    encrypted_content = encrypt(content, key)
    metadata = (
        encode_tool_metadata(metadata_json, key) if metadata_json else None
    )

    async with db_session(config) as db:
        if chat_session_id:
            await db.execute(
                "INSERT OR IGNORE INTO agent_chat_sessions (id, user_id) "
                "VALUES (?, ?)",
                (chat_session_id, user_id),
            )
        await db.execute(
            "INSERT INTO agent_messages "
            "(id, user_id, chat_session_id, role, content, tool_name, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, user_id, chat_session_id, "assistant",
             encrypted_content, None, metadata),
        )
        if chat_session_id:
            await db.execute(
                "UPDATE agent_chat_sessions SET message_count = "
                "(SELECT COUNT(*) FROM agent_messages WHERE chat_session_id = ?) "
                "WHERE id = ?",
                (chat_session_id, chat_session_id),
            )
        await db.commit()

    return msg_id
