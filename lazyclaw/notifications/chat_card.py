"""Chat-card leg of the Notification Spine (piece 3 of the 2026-07-16 design).

When the user's notification channel includes the app (``app`` | ``both``),
a proactive ping ALSO lands as a persisted assistant-role message in the
user's PRIMARY chat session, so the mobile/web chat shows it inline instead
of only in the Notification Center feed.

The row is written through :mod:`lazyclaw.memory.chat_message_store` (same
encryption + insert shape as the agent's turn writer) and carries the
``notification_card`` metadata marker. That marker makes the row UI-ONLY:
``memory/compressor.py`` excludes it from the LLM context and
``memory/daily_log.py`` skips it when summarizing — a notification must
never be re-read by the brain as something it previously said.

Interface contract (consumed lazily by ``spine._fan_out_chat_card`` and the
legacy funnels): ``await chat_card.emit(config, user_id, notif)`` where
``notif`` is the decrypted feed-row dict from
:func:`lazyclaw.notifications.feed_store.record_notification`
(``{id, kind, title, body, created_at, ...}``).

Fail-open: never raises into the ping path — errors are logged and
swallowed, mirroring the spine's transport contract.
"""

from __future__ import annotations

import logging
from typing import Any

from lazyclaw.memory.chat_message_store import (
    append_assistant_message,
    build_notification_metadata,
)

logger = logging.getLogger(__name__)


def compose_card_text(title: str | None, body: str | None) -> str:
    """Render the card body as clean ``{title}\\n{body}`` text.

    Titles are commonly derived from the body's first line
    (``push._derive_push_title``); repeating them verbatim would render a
    stuttering card, so a title the body already starts with is dropped.
    """
    t = (title or "").strip()
    b = (body or "").strip()
    if not t:
        return b
    if not b:
        return t
    if b.startswith(t):
        return b
    return f"{t}\n{b}"


async def emit(config: Any, user_id: str, notif: dict) -> str | None:
    """Append one notification as an assistant row in the primary session.

    Returns the new ``agent_messages`` id, or ``None`` when nothing was
    written (empty text or any failure). NEVER raises.
    """
    try:
        if not user_id or not isinstance(notif, dict):
            return None
        kind = str(notif.get("kind") or "info")
        text = compose_card_text(notif.get("title"), notif.get("body"))
        if not text:
            return None

        from lazyclaw.runtime.session_resolver import get_primary_session_id

        session_id = await get_primary_session_id(config, user_id)
        msg_id = await append_assistant_message(
            config, user_id, session_id, text,
            metadata_json=build_notification_metadata(kind),
        )
        logger.debug(
            "[chat_card] wrote notification row user=%s kind=%s msg=%s",
            user_id[:8], kind, msg_id[:8],
        )
        return msg_id
    except Exception:
        logger.warning("chat_card emit failed", exc_info=True)
        return None
