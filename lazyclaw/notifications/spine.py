"""The Notification Spine — one typed emit API every producer calls.

Replaces the historical scatter of four funnels (``dispatch.deliver``,
``push.push_telegram``, ``heartbeat_push.deliver_heartbeat_push``,
``telegram_notifier.TelegramNotifier``). A single :func:`notify` call:

  1. ALWAYS records a durable row to the Notification Center feed
     (:mod:`lazyclaw.notifications.feed_store`) — the in-app center is the
     source of truth for "what happened", independent of the Telegram toggle;
  2. sends to Telegram when the user's channel includes it and the notification
     isn't ``silent``;
  3. fans out to the real-time / push / chat-card transports through optional
     hooks that later pieces (ntfy push, WS notification frame, chat card) wire
     in — absent hooks are silent no-ops, so this module ships standalone.

The ``telegram | app | both`` channel toggle now controls **loudness only**
(Telegram + push). It NEVER gates the durable feed record — that is what makes
the Notification Center trustworthy as the single place to see everything.

Fail-open: any transport error is swallowed and logged. A producer must never
crash because a notification could not be delivered.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from lazyclaw.notifications.channel import (
    get_notification_channel,
    should_send_telegram,
)
from lazyclaw.notifications.feed_store import record_notification
from lazyclaw.notifications.push import _send_telegram_raw

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Notification:
    """The persisted, decrypted notification returned by :func:`notify`."""

    id: str
    kind: str
    severity: str
    title: str
    body: str
    created_at: str
    deep_link: dict | None = None
    actions: list = field(default_factory=list)
    meta: dict | None = None
    read_at: str | None = None
    repeat_count: int = 1
    telegram_sent: bool = False


def _keyboard_from_actions(
    actions: Sequence[dict] | None,
) -> list[list[dict]] | None:
    """Derive a Telegram inline keyboard from structured ``actions``.

    Each action ``{"label","action_id"}`` becomes one button
    ``{"text","callback_data"}``. Buttons lacking either field are skipped.
    One button per row (vertical stack) — matches the existing watcher/task
    keyboards. Returns ``None`` when there is nothing to render.
    """
    if not actions:
        return None
    rows: list[list[dict]] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        label = a.get("label")
        action_id = a.get("action_id") or a.get("callback_data")
        if not label or not action_id:
            continue
        rows.append([{"text": str(label), "callback_data": str(action_id)}])
    return rows or None


async def _fan_out_realtime(config: Any, user_id: str, notif: dict) -> None:
    """Push the notification to the open-app WS + ntfy transports (piece 2).

    Wired lazily: if ``lazyclaw.notifications.realtime`` isn't present yet the
    call is a no-op. Best-effort — never raises into the producer.
    """
    try:
        from lazyclaw.notifications import realtime  # type: ignore
    except ImportError:
        return
    try:
        await realtime.emit(config, user_id, notif)  # type: ignore[attr-defined]
    except Exception:
        logger.debug("realtime fan-out failed", exc_info=True)


async def _fan_out_chat_card(config: Any, user_id: str, notif: dict) -> None:
    """Drop a rich card into the chat stream for important notifications (piece 3).

    Wired lazily via ``lazyclaw.notifications.chat_card``; no-op until present.
    """
    try:
        from lazyclaw.notifications import chat_card  # type: ignore
    except ImportError:
        return
    try:
        await chat_card.emit(config, user_id, notif)  # type: ignore[attr-defined]
    except Exception:
        logger.debug("chat-card fan-out failed", exc_info=True)


async def notify(
    config: Any,
    user_id: str,
    *,
    kind: str,
    title: str,
    body: str,
    severity: str = "normal",
    deep_link: dict | None = None,
    actions: Sequence[dict] | None = None,
    dedup_key: str | None = None,
    thread_ref: dict | None = None,
    telegram: bool = True,
    telegram_text: str | None = None,
    inline_keyboard: Sequence[Sequence[dict]] | None = None,
    telegram_parse_mode: str | None = None,
    chat_card: bool = False,
    silent: bool = False,
) -> Notification:
    """Emit one notification through the spine.

    Always records a durable feed row (the Notification Center). Sends to
    Telegram when the resolved channel includes it and ``silent`` is False and
    ``telegram`` is True. Fans out to real-time / chat-card transports.

    ``deep_link`` — structured tap target ``{"type","id","channel"}``.
    ``actions``  — button specs ``[{"label","action_id","style"}]`` rendered on
                   BOTH the Telegram keyboard (unless ``inline_keyboard`` is
                   given) and the in-app card.
    ``dedup_key`` — collapse a recent unread repeat instead of appending.
    ``thread_ref`` — legacy deep-link sidecar (channel_message); merged to meta.
    ``silent``   — record durably but suppress all loud fan-out.
    """
    meta = {"thread_ref": thread_ref} if thread_ref is not None else None
    actions_list = list(actions) if actions else None

    # 1. durable record — ALWAYS (best-effort; never crash the producer)
    try:
        rec = await record_notification(
            config,
            user_id,
            kind,
            title,
            body,
            meta=meta,
            severity=severity,
            deep_link=deep_link,
            actions=actions_list,
            dedup_key=dedup_key,
        )
    except Exception:
        logger.warning("notify: durable feed record failed", exc_info=True)
        rec = {
            "id": "",
            "kind": kind,
            "severity": severity,
            "title": title or "",
            "body": body or "",
            "meta": meta,
            "deep_link": deep_link,
            "actions": actions_list,
            "read_at": None,
            "created_at": "",
            "repeat_count": 1,
        }

    telegram_sent = False
    if not silent and telegram:
        try:
            channel = await get_notification_channel(config, user_id)
            if should_send_telegram(channel):
                text = telegram_text or (
                    f"{title}\n{body}".strip() if title else body
                )
                kb = inline_keyboard or _keyboard_from_actions(actions_list)
                telegram_sent = await _send_telegram_raw(
                    config,
                    text,
                    parse_mode=telegram_parse_mode,
                    inline_keyboard=kb,
                )
        except Exception:
            logger.warning("notify: telegram fan-out failed", exc_info=True)

    if not silent:
        await _fan_out_realtime(config, user_id, rec)
        if chat_card:
            await _fan_out_chat_card(config, user_id, rec)

    return Notification(
        id=rec.get("id", ""),
        kind=rec.get("kind", kind),
        severity=rec.get("severity", severity),
        title=rec.get("title", title or ""),
        body=rec.get("body", body or ""),
        created_at=rec.get("created_at", ""),
        deep_link=rec.get("deep_link"),
        actions=rec.get("actions") or [],
        meta=rec.get("meta"),
        read_at=rec.get("read_at"),
        repeat_count=int(rec.get("repeat_count", 1)),
        telegram_sent=telegram_sent,
    )
