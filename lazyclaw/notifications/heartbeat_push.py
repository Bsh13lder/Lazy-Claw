"""Preference-aware routing for the heartbeat daemon's ``telegram_push`` callback.

The heartbeat daemon fires reminders, nags, task pulses, watcher notices,
recurring-expense alerts and EOD summaries through one callback:
``telegram_push(text, reply_markup=None)`` wired in ``cli.py`` /
``cli_tui.py``. Historically that callback called ``bot.send_message``
directly — bypassing the per-user channel toggle (``telegram`` | ``app`` |
``both``), so "in-app only" users kept receiving Telegram pushes and never
saw those entries in the mobile feed.

This module is the single funnel both wiring sites now call. Since the
Notification Spine (2026-07-16) the durable in-app feed row is recorded
ALWAYS; the toggle controls Telegram loudness only:

  * ``telegram`` (default) → record to the feed AND send to Telegram.
  * ``app``                → record to the feed, skip Telegram.
  * ``both``               → record to the feed AND send to Telegram.

Fail-open contract: any routing/lookup error degrades to the legacy
Telegram-only path so a corrupt setting can never swallow a reminder.
Inline keyboards (task ✅ Done buttons etc.) live inside the caller's
``telegram_send`` closure — in ``app`` mode the feed entry carries the text
only (the mobile feed has no action buttons yet).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable

from lazyclaw.notifications.app_fanout import fan_out_app_ping
from lazyclaw.notifications.channel import (
    get_notification_channel,
    resolve_admin_user_id,
    should_send_chat,
    should_send_telegram,
)
from lazyclaw.notifications.feed_store import record_notification
from lazyclaw.notifications.push import _derive_push_title

logger = logging.getLogger(__name__)


def _dedup_key(kind: str, text: str) -> str:
    """Collapse-key for one heartbeat push, derived from its rendered text.

    `record_notification` merges a recent unread row carrying the same key and
    bumps `repeat_count` instead of appending — but until now NO caller passed
    a key, so all 418 rows in the live feed had `dedup_key IS NULL` and nothing
    could ever collapse. A single day showed six pairs of identical heartbeat
    rows written 3-6 ms apart.

    Content is the right granularity here: this funnel receives only the
    rendered message, so two pushes with identical text within the dedup window
    ARE the same event, while two different tasks firing in one tick render
    different text and must stay separate rows. Hashed rather than stored raw
    because `dedup_key` is a PLAINTEXT column and the text is user content.
    """
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    return f"{kind}:{digest[:32]}"


async def deliver_heartbeat_push(
    config: Any,
    text: str,
    *,
    telegram_send: Callable[[], Awaitable[None]],
    kind: str = "heartbeat",
    dedup_key: str | None = None,
    dedup_window: timedelta | None = None,
) -> None:
    """Route one heartbeat push through the user's notification channel.

    ``telegram_send`` performs the actual Telegram delivery (the caller owns
    the bot handle, retry logic and any inline keyboard). It is awaited only
    when the resolved channel includes Telegram; its errors propagate so the
    caller can log them with context.

    ``dedup_key`` lets a caller declare "these pushes are the same EVENT" when
    the text alone cannot say so. The task-reminder ladder is the motivating
    case: a single dose fires a T-1h heads-up, a T-30m heads-up, the at-time
    nag, then escalations #2 and #3 — five rows whose text legitimately differs
    every time (each carries its own lead or nag counter), so the content hash
    can never merge them. Only the daemon knows they are one task occurrence.
    Omit it and the content hash still guards verbatim repeats.
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

    # Durable record — ALWAYS, regardless of the channel toggle. The in-app
    # Notification Center is the source of truth for "what happened"; the
    # toggle controls Telegram loudness only (Spine, 2026-07-16). Only the
    # no-admin fail-open path skips the record.
    rec: dict | None = None
    if admin_uid:
        try:
            rec = await record_notification(
                config, admin_uid, kind, _derive_push_title(text), text,
                dedup_key=dedup_key or _dedup_key(kind, text),
                dedup_window=dedup_window,
            )
        except Exception:
            logger.warning("heartbeat push feed record failed", exc_info=True)

    # App legs — persisted chat card + realtime WS frame — for app/both
    # channels. Best-effort (fan_out_app_ping never raises); falls back to a
    # synthetic notif dict when the feed record itself failed so an app-only
    # user still sees the ping in chat.
    if admin_uid and should_send_chat(channel):
        notif = rec or {
            "id": "",
            "kind": kind,
            "title": _derive_push_title(text),
            "body": text,
            "created_at": "",
        }
        await fan_out_app_ping(config, admin_uid, notif)

    if should_send_telegram(channel):
        await telegram_send()
