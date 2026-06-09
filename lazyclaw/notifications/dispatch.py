"""Single notification delivery funnel.

Always records channel-message notifications to the in-app feed (so the
Flutter app receives them regardless of the telegram/app/both toggle), and
sends to Telegram only when the user's channel setting includes it.
"""
from __future__ import annotations

from typing import Any, Sequence

from lazyclaw.notifications.channel import (
    get_notification_channel,
    should_record_feed,
    should_send_telegram,
)
from lazyclaw.notifications.feed_store import record_notification
from lazyclaw.notifications.push import _send_telegram_raw

# kinds that must always reach the phone regardless of the channel toggle
_ALWAYS_FEED_KINDS = frozenset({"channel_message", "conversation_result"})


async def deliver(
    config: Any,
    user_id: str,
    *,
    title: str,
    body: str,
    kind: str = "info",
    inline_keyboard: Sequence[Sequence[dict]] | None = None,
    thread_ref: dict | None = None,
) -> bool:
    """Record to feed (per channel + always for channel messages), then maybe Telegram."""
    channel = await get_notification_channel(config, user_id)
    if should_record_feed(channel) or kind in _ALWAYS_FEED_KINDS:
        meta = {"thread_ref": thread_ref} if thread_ref is not None else None
        await record_notification(config, user_id, kind, title, body, meta=meta)
    if should_send_telegram(channel):
        text = f"{title}\n{body}".strip() if title else body
        return await _send_telegram_raw(
            config, text, parse_mode=None, inline_keyboard=inline_keyboard,
        )
    return True
