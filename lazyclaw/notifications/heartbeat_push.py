"""Preference-aware routing for the heartbeat daemon's ``telegram_push`` callback.

The heartbeat daemon fires reminders, nags, task pulses, watcher notices,
recurring-expense alerts and EOD summaries through one callback:
``telegram_push(text, reply_markup=None)`` wired in ``cli.py`` /
``cli_tui.py``. Historically that callback called ``bot.send_message``
directly — bypassing the per-user channel toggle (``telegram`` | ``app`` |
``both``), so "in-app only" users kept receiving Telegram pushes and never
saw those entries in the mobile feed.

This module is the single funnel both wiring sites now call:

  * ``telegram`` (default) → Telegram send only (legacy behaviour).
  * ``app``                → record to the in-app feed, skip Telegram.
  * ``both``               → record to the feed AND send to Telegram.

Fail-open contract: any routing/lookup error degrades to the legacy
Telegram-only path so a corrupt setting can never swallow a reminder.
Inline keyboards (task ✅ Done buttons etc.) live inside the caller's
``telegram_send`` closure — in ``app`` mode the feed entry carries the text
only (the mobile feed has no action buttons yet).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from lazyclaw.notifications.channel import (
    get_notification_channel,
    resolve_admin_user_id,
    should_record_feed,
    should_send_telegram,
)
from lazyclaw.notifications.feed_store import record_notification
from lazyclaw.notifications.push import _derive_push_title

logger = logging.getLogger(__name__)


async def deliver_heartbeat_push(
    config: Any,
    text: str,
    *,
    telegram_send: Callable[[], Awaitable[None]],
    kind: str = "heartbeat",
) -> None:
    """Route one heartbeat push through the user's notification channel.

    ``telegram_send`` performs the actual Telegram delivery (the caller owns
    the bot handle, retry logic and any inline keyboard). It is awaited only
    when the resolved channel includes Telegram; its errors propagate so the
    caller can log them with context.
    """
    admin_uid: str | None = None
    channel = "telegram"  # fail-open default: legacy Telegram-only
    try:
        admin_uid = await resolve_admin_user_id(config)
        if admin_uid:
            channel = await get_notification_channel(config, admin_uid)
    except Exception:
        logger.debug(
            "heartbeat push channel routing failed; defaulting telegram",
            exc_info=True,
        )
        admin_uid = None
        channel = "telegram"

    if admin_uid and should_record_feed(channel):
        try:
            await record_notification(
                config, admin_uid, kind, _derive_push_title(text), text,
            )
        except Exception:
            logger.warning("heartbeat push feed record failed", exc_info=True)

    if should_send_telegram(channel):
        await telegram_send()
