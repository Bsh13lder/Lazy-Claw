"""Realtime leg of the Notification Spine (piece 2 of the 2026-07-16 design).

In-memory per-user pub/sub for notification WS frames, mirroring
:mod:`lazyclaw.runtime.task_event_bus` (bounded ring for reconnect paint,
drop-on-backpressure, per-user isolation, zero persistence — the durable
copy is the feed row in ``feed_store``).

Frame contract (the mobile client builds against EXACTLY this shape)::

    {"type": "notification", "id": "<feed row id>", "kind": "<kind>",
     "title": "<title>", "body": "<body>", "created_at": "<ISO8601 UTC>"}

Interface contract (consumed lazily by ``spine._fan_out_realtime`` and the
legacy funnels): ``await realtime.emit(config, user_id, notif)`` with the
decrypted feed-row dict. ``emit`` NEVER raises into the ping path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import uuid4

logger = logging.getLogger(__name__)

RING_SIZE = 20
SUBSCRIBER_QUEUE = 16
# Frames older than this are not replayed on reconnect — matches the
# task_event_bus initial-paint window used by gateway/routes/chat_ws.py.
MAX_AGE_S = 300.0

FRAME_TYPE = "notification"


@dataclass(frozen=True)
class NotificationEvent:
    """Immutable notification frame payload."""

    user_id: str
    id: str
    kind: str
    title: str
    body: str
    created_at: str
    ts: float = field(default_factory=time.time)

    def to_frame(self) -> dict:
        """JSON-safe WS frame — EXACTLY the published contract, no extras."""
        return {
            "type": FRAME_TYPE,
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "created_at": self.created_at,
        }


class _UserChannel:
    __slots__ = ("ring", "subscribers")

    def __init__(self) -> None:
        self.ring: deque[NotificationEvent] = deque(maxlen=RING_SIZE)
        self.subscribers: list[asyncio.Queue] = []


_channels: dict[str, _UserChannel] = {}
_lock = asyncio.Lock()


def _channel(user_id: str) -> _UserChannel:
    ch = _channels.get(user_id)
    if ch is None:
        ch = _UserChannel()
        _channels[user_id] = ch
    return ch


def publish(event: NotificationEvent) -> None:
    """Publish a frame. Non-blocking; drops on subscriber backpressure."""
    if not event.user_id:
        return
    ch = _channel(event.user_id)
    ch.ring.append(event)
    logger.debug(
        "[notifbus] publish kind=%s user=%s subscribers=%d",
        event.kind, event.user_id[:8], len(ch.subscribers),
    )
    for q in list(ch.subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.debug(
                    "[notifbus] frame dropped kind=%s user=%s (slow subscriber)",
                    event.kind, event.user_id[:8],
                )


async def subscribe(user_id: str) -> AsyncIterator[NotificationEvent]:
    """Async iterator over future notification frames for a user."""
    ch = _channel(user_id)
    q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
    async with _lock:
        ch.subscribers.append(q)
    try:
        while True:
            evt = await q.get()
            yield evt
    finally:
        async with _lock:
            try:
                ch.subscribers.remove(q)
            except ValueError:
                logger.debug(
                    "[notifbus] unsubscribe: queue already removed user=%s",
                    user_id[:8] if user_id else None,
                )


def recent_events(
    user_id: str,
    limit: int = 10,
    max_age_s: float | None = MAX_AGE_S,
) -> list[NotificationEvent]:
    """Latest N frames for initial paint on (re)connect, age-bounded."""
    ch = _channels.get(user_id)
    if ch is None or limit <= 0:
        return []
    events = list(ch.ring)[-limit:]
    if max_age_s is not None:
        cutoff = time.time() - max_age_s
        events = [e for e in events if e.ts >= cutoff]
    return events


def clear_user(user_id: str) -> None:
    """Drop all state for a user (logout, session reset)."""
    _channels.pop(user_id, None)


def event_from_notif(user_id: str, notif: dict) -> NotificationEvent:
    """Build a frame event from a decrypted feed-row dict (fail-safe fields).

    A hint frame may carry an id that matches no feed row (Class-A live
    hints from the heartbeat notifier) — a fresh uuid keeps the schema
    intact and the client's history-merge dedup simply won't match it.
    """
    return NotificationEvent(
        user_id=user_id,
        id=str(notif.get("id") or "") or uuid4().hex,
        kind=str(notif.get("kind") or "info"),
        title=str(notif.get("title") or ""),
        body=str(notif.get("body") or ""),
        created_at=(
            str(notif.get("created_at") or "")
            or datetime.now(timezone.utc).isoformat()
        ),
    )


async def emit(config: Any, user_id: str, notif: dict) -> None:
    """Spine transport interface — publish one feed-row dict as a frame.

    ``config`` is unused (in-memory bus) but kept for the spine's uniform
    transport signature. NEVER raises into the ping path.
    """
    try:
        if not user_id or not isinstance(notif, dict):
            return
        publish(event_from_notif(user_id, notif))
    except Exception:
        logger.debug("realtime emit failed", exc_info=True)
