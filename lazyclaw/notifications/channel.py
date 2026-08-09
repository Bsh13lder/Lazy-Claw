"""Per-user notification channel routing — ``telegram`` | ``app`` | ``both``.

Single-user self-hosted: the routing switch is a GLOBAL setting stored on the
admin user's ``users.settings.notifications.channel`` (mirrors how ``eco`` lives
under ``users.settings.eco``). The admin user is the first registered user —
the same resolution the Telegram adapter uses to bind the admin chat.

This module is the ONE place the two emit funnels
(:func:`lazyclaw.notifications.push.push_telegram` and
:class:`lazyclaw.notifications.telegram_notifier.TelegramNotifier`) consult to
decide whether a notification goes to Telegram, the in-app feed, or both.

Default is ``telegram`` for backward compatibility: existing installs keep
behaving exactly as before until the user flips the switch in the mobile app.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

VALID_CHANNELS: tuple[str, ...] = ("telegram", "app", "both")
DEFAULT_CHANNEL = "telegram"


def normalize_channel(value: Any) -> str:
    """Coerce any stored/incoming value to a valid channel (fail-safe).

    Anything unrecognised falls back to ``telegram`` so a corrupt setting can
    never silently swallow notifications.
    """
    if not isinstance(value, str):
        return DEFAULT_CHANNEL
    v = value.strip().lower()
    return v if v in VALID_CHANNELS else DEFAULT_CHANNEL


def should_send_telegram(channel: str) -> bool:
    """Pure gating decision: does this channel deliver to Telegram?"""
    return normalize_channel(channel) in ("telegram", "both")


def should_record_feed(channel: str) -> bool:
    """Pure gating decision: does this channel record to the in-app feed?"""
    return normalize_channel(channel) in ("app", "both")


def should_send_chat(channel: str) -> bool:
    """Pure gating decision: does this channel drop pings into the chat?

    ``app`` / ``both`` users get proactive pings as persisted chat-card
    messages (+ realtime WS frames) in their primary session — see
    :mod:`lazyclaw.notifications.chat_card`.
    """
    return normalize_channel(channel) in ("app", "both")


async def resolve_admin_user_id(config: Config) -> str | None:
    """Resolve the admin user id for a server-originated notification.

    Single-user self-hosted, but real installs accumulate stale rows
    ("default" from first boot, test users). The OWNER is the user whose
    Telegram admin chat is bound (``settings.telegram_chat_id`` — written
    by the adapter's /start claim and /link). Resolution order:

      1. the user with a Telegram chat binding (oldest such row wins);
      2. legacy fallback: the first registered user.

    Picking by created_at alone routed every preference READ and feed
    WRITE to the stale "default" row — Telegram-only behaviour persisted
    no matter what the real user toggled (2026-06-10 incident).
    Returns ``None`` (best-effort) on any error so callers can fall back
    to legacy Telegram-only behaviour.
    """
    try:
        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id FROM users "
                "WHERE json_extract(settings, '$.telegram_chat_id') IS NOT NULL "
                "ORDER BY created_at LIMIT 1"
            )
            row = await cur.fetchone()
            if row:
                return row[0]
            cur = await db.execute(
                "SELECT id FROM users ORDER BY created_at LIMIT 1"
            )
            row = await cur.fetchone()
            if row:
                return row[0]
    except Exception:
        logger.debug("resolve_admin_user_id failed", exc_info=True)
    return None


async def get_notification_channel(config: Config, user_id: str) -> str:
    """Return the user's notification channel, defaulting to ``telegram``."""
    try:
        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT settings FROM users WHERE id = ?", (user_id,)
            )
            row = await cur.fetchone()
    except Exception:
        logger.debug("get_notification_channel read failed", exc_info=True)
        return DEFAULT_CHANNEL

    if not row or not row[0]:
        return DEFAULT_CHANNEL
    try:
        settings = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return DEFAULT_CHANNEL

    notif = settings.get("notifications")
    if not isinstance(notif, dict):
        return DEFAULT_CHANNEL
    return normalize_channel(notif.get("channel"))


async def set_notification_channel(
    config: Config, user_id: str, channel: str,
) -> str:
    """Persist the user's notification channel. Returns the normalised value.

    Immutable update: rebuilds the settings dict so unrelated keys (``eco``,
    etc.) are preserved. Raises :class:`ValueError` on an invalid channel.
    """
    norm = (channel or "").strip().lower()
    if norm not in VALID_CHANNELS:
        raise ValueError(
            f"Invalid channel: {channel!r}. "
            f"Use one of: {', '.join(VALID_CHANNELS)}"
        )

    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        row = await cur.fetchone()

    current: dict = {}
    if row and row[0]:
        try:
            current = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            current = {}
    if not isinstance(current, dict):
        current = {}

    notif = current.get("notifications")
    new_notif = dict(notif) if isinstance(notif, dict) else {}
    new_notif["channel"] = norm

    new_settings = dict(current)
    new_settings["notifications"] = new_notif

    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(new_settings), user_id),
        )
        await db.commit()

    return norm
