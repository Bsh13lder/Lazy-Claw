"""Context compression engine — sliding window + persistent summaries.

Orchestrates the compression pipeline:
1. Load all messages for the session
2. Keep last WINDOW_SIZE messages in full detail
3. Summarize older messages (reuse stored summaries when available)
4. Return [summary] + recent_messages for the agent's context
"""

from __future__ import annotations

import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, encrypt, decrypt
from lazyclaw.db.connection import db_session
from lazyclaw.llm.eco_router import EcoRouter
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.memory.classifier import classify_message
from lazyclaw.memory.summarizer import summarize_chunk

logger = logging.getLogger(__name__)

# Number of recent messages to keep in full detail
WINDOW_SIZE = 15


async def compress_history(
    config: Config,
    eco_router: EcoRouter,
    user_id: str,
    chat_session_id: str | None,
    raw_messages: list[tuple],
) -> list[LLMMessage]:
    """Compress conversation history using sliding window + summaries.

    Args:
        config: App config
        eco_router: LLM router for summarization calls
        user_id: User scope
        chat_session_id: Current chat session (for summary lookup)
        raw_messages: All messages as (id, role, content, tool_name, metadata)
                     ordered by created_at ASC

    Returns:
        List of LLMMessage ready for the agent context:
        [summary_message (if any), ...recent_messages]
    """
    key = derive_server_key(config.server_secret, user_id)

    # Decrypt all messages
    decrypted = []
    for msg_id, role, content, tool_name, metadata in raw_messages:
        text = decrypt(content, key) if content.startswith("enc:") else content
        has_tool_calls = bool(metadata)
        decrypted.append({
            "id": msg_id,
            "role": role,
            "content": text,
            "tool_name": tool_name,
            "has_tool_calls": has_tool_calls,
        })

    # If within window, no compression needed
    if len(decrypted) <= WINDOW_SIZE:
        return _to_llm_messages(decrypted)

    # Split: older messages (to compress) + recent (keep full)
    older = decrypted[:-WINDOW_SIZE]
    recent = decrypted[-WINDOW_SIZE:]

    # Try to load existing summary for this chunk
    summary_text = await _load_existing_summary(
        config, user_id, chat_session_id, older
    )

    if not summary_text:
        # No existing summary — generate one
        classifications = [
            classify_message(
                role=m["role"],
                content=m["content"],
                tool_name=m.get("tool_name"),
                has_tool_calls=m.get("has_tool_calls", False),
            )
            for m in older
        ]

        summary_text = await summarize_chunk(
            eco_router, user_id, older, classifications
        )

        # Persist the summary
        await _store_summary(
            config, user_id, chat_session_id,
            from_id=older[0]["id"],
            to_id=older[-1]["id"],
            count=len(older),
            content=summary_text,
        )

    # Build final context: summary + recent messages
    result = [
        LLMMessage(
            role="system",
            content=f"## Earlier conversation summary\n{summary_text}",
        )
    ]
    result.extend(_to_llm_messages(recent))
    return result


def _to_llm_messages(messages: list[dict]) -> list[LLMMessage]:
    """Convert message dicts to LLMMessage objects."""
    result = []
    for m in messages:
        result.append(LLMMessage(
            role=m["role"],
            content=m["content"],
            tool_call_id=m.get("tool_name"),
        ))
    return result


async def _load_existing_summary(
    config: Config,
    user_id: str,
    chat_session_id: str | None,
    older_messages: list[dict],
) -> str | None:
    """Check if we already have a summary covering these messages."""
    if not older_messages:
        return None

    from_id = older_messages[0]["id"]
    to_id = older_messages[-1]["id"]
    key = derive_server_key(config.server_secret, user_id)

    async with db_session(config) as db:
        row = await db.execute(
            "SELECT content FROM message_summaries "
            "WHERE user_id = ? AND from_message_id = ? AND to_message_id = ?",
            (user_id, from_id, to_id),
        )
        result = await row.fetchone()

    if not result:
        return None

    content = result[0]
    return decrypt(content, key) if content.startswith("enc:") else content


async def _store_summary(
    config: Config,
    user_id: str,
    chat_session_id: str | None,
    from_id: str,
    to_id: str,
    count: int,
    content: str,
) -> str:
    """Store an encrypted summary. Returns the summary ID."""
    key = derive_server_key(config.server_secret, user_id)
    summary_id = str(uuid4())
    encrypted = encrypt(content, key)

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO message_summaries "
            "(id, user_id, chat_session_id, from_message_id, to_message_id, "
            "message_count, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (summary_id, user_id, chat_session_id, from_id, to_id,
             count, encrypted),
        )
        await db.commit()

    logger.info("Stored summary %s covering %d messages", summary_id, count)
    return summary_id


async def get_compression_stats(
    config: Config, user_id: str, chat_session_id: str | None = None
) -> dict:
    """Get compression statistics for a user or session."""
    async with db_session(config) as db:
        # Count summaries
        if chat_session_id:
            row = await db.execute(
                "SELECT COUNT(*), COALESCE(SUM(message_count), 0) "
                "FROM message_summaries "
                "WHERE user_id = ? AND chat_session_id = ?",
                (user_id, chat_session_id),
            )
        else:
            row = await db.execute(
                "SELECT COUNT(*), COALESCE(SUM(message_count), 0) "
                "FROM message_summaries WHERE user_id = ?",
                (user_id,),
            )
        summary_row = await row.fetchone()

        # Count total messages
        row2 = await db.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE user_id = ?",
            (user_id,),
        )
        msg_row = await row2.fetchone()

    summary_count = summary_row[0] if summary_row else 0
    compressed_messages = summary_row[1] if summary_row else 0
    total_messages = msg_row[0] if msg_row else 0

    return {
        "summary_count": summary_count,
        "compressed_messages": compressed_messages,
        "total_messages": total_messages,
        "active_messages": total_messages - compressed_messages,
        "compression_ratio": (
            round(compressed_messages / total_messages * 100, 1)
            if total_messages > 0 else 0
        ),
        "window_size": WINDOW_SIZE,
    }


async def force_recompress(
    config: Config,
    eco_router: EcoRouter,
    user_id: str,
    chat_session_id: str | None = None,
) -> int:
    """Delete existing summaries and regenerate. Returns count deleted."""
    async with db_session(config) as db:
        if chat_session_id:
            result = await db.execute(
                "DELETE FROM message_summaries "
                "WHERE user_id = ? AND chat_session_id = ?",
                (user_id, chat_session_id),
            )
        else:
            result = await db.execute(
                "DELETE FROM message_summaries WHERE user_id = ?",
                (user_id,),
            )
        await db.commit()
        deleted = result.rowcount

    logger.info("Deleted %d summaries for user %s, will regenerate on next chat", deleted, user_id)
    return deleted
