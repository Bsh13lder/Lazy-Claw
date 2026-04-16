"""In-memory pub/sub for live browser activity events.

The CDP backend publishes events (click, type, goto, screenshot, checkpoint,
alert, ...) into this bus. The web chat WebSocket subscribes per user and
forwards events to the BrowserCanvas. Telegram and other channels can also
subscribe.

Design constraints:
- Zero LLM token cost — events never re-enter the agent context.
- Bounded memory — per-user ring buffer, plus per-user single-frame thumbnail.
- Per-user isolation — events keyed on user_id, never cross-routed.
- Drop on backpressure — slow subscribers never block the agent loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Per-user ring buffer length (initial paint shows last N).
RING_SIZE = 50
# Per-subscriber queue depth before we start dropping.
SUBSCRIBER_QUEUE = 32
# Latest WebP thumbnail per user — (bytes, url, ts) tuple.
# Storing the URL lets us know when the cached thumb is stale (URL moved on).
_latest_thumbs: dict[str, tuple[bytes, str | None, float]] = {}
# When live mode is on for a user, the CDP backend captures a thumbnail on
# *every* action instead of only on URL change. Auto-disabled at expiry.
_live_mode_until: dict[str, float] = {}
LIVE_MODE_DEFAULT_SECONDS = 300  # 5 minutes


@dataclass(frozen=True)
class BrowserEvent:
    """Immutable browser activity event."""

    user_id: str
    kind: str               # action | navigate | snapshot | checkpoint | alert | takeover | done
    ts: float = field(default_factory=time.time)
    action: str | None = None        # click | type | goto | scroll | screenshot | tabs | press_key
    target: str | None = None        # selector / ref-id / role description
    url: str | None = None
    title: str | None = None
    detail: str | None = None        # human-readable e.g. "Clicked 'Sign in'"
    extra: dict | None = None        # checkpoint name, takeover url, alert payload

    def to_frame(self) -> dict:
        """Convert to a JSON-safe WebSocket frame payload."""
        d = asdict(self)
        # Drop None-valued keys to keep frames small.
        return {k: v for k, v in d.items() if v is not None}


class _UserChannel:
    """Per-user state: ring buffer + active subscriber queues."""

    __slots__ = ("ring", "subscribers")

    def __init__(self) -> None:
        self.ring: deque[BrowserEvent] = deque(maxlen=RING_SIZE)
        self.subscribers: list[asyncio.Queue] = []


_channels: dict[str, _UserChannel] = {}
_lock = asyncio.Lock()


def _channel(user_id: str) -> _UserChannel:
    ch = _channels.get(user_id)
    if ch is None:
        ch = _UserChannel()
        _channels[user_id] = ch
    return ch


def publish(event: BrowserEvent) -> None:
    """Publish an event. Non-blocking — drops on subscriber backpressure."""
    if not event.user_id:
        return
    ch = _channel(event.user_id)
    ch.ring.append(event)
    for q in list(ch.subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Subscriber lagging — drop oldest in their queue, push newest.
            try:
                q.get_nowait()
                q.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.debug("Browser event dropped for %s (subscriber slow)", event.user_id)


async def subscribe(user_id: str) -> AsyncIterator[BrowserEvent]:
    """Async iterator over future events for a user."""
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
                pass


def recent_events(user_id: str, limit: int = 8) -> list[BrowserEvent]:
    """Return the latest N events for initial paint."""
    ch = _channels.get(user_id)
    if ch is None:
        return []
    if limit <= 0:
        return []
    return list(ch.ring)[-limit:]


def latest_state(user_id: str) -> dict | None:
    """Distill the latest URL + title from the ring buffer (cheap)."""
    ch = _channels.get(user_id)
    if ch is None or not ch.ring:
        return None
    url = title = None
    for evt in reversed(ch.ring):
        if evt.url and not url:
            url = evt.url
        if evt.title and not title:
            title = evt.title
        if url and title:
            break
    if not url and not title:
        return None
    return {"url": url, "title": title, "ts": ch.ring[-1].ts}


def set_thumbnail(user_id: str, png_bytes: bytes, url: str | None = None) -> None:
    """Store the latest thumbnail bytes for a user (overwrites previous)."""
    if not user_id or not png_bytes:
        return
    _latest_thumbs[user_id] = (png_bytes, url, time.time())


def get_thumbnail(user_id: str) -> bytes | None:
    entry = _latest_thumbs.get(user_id)
    return entry[0] if entry else None


def get_thumbnail_meta(user_id: str) -> tuple[str | None, float] | None:
    """Return (url, ts) for the cached thumbnail, or None."""
    entry = _latest_thumbs.get(user_id)
    if not entry:
        return None
    return entry[1], entry[2]


def is_thumbnail_fresh(user_id: str, current_url: str | None, max_age_s: float = 60.0) -> bool:
    """A thumbnail is fresh if its URL still matches and it's recent."""
    entry = _latest_thumbs.get(user_id)
    if not entry:
        return False
    _, thumb_url, ts = entry
    age = time.time() - ts
    if age > max_age_s:
        return False
    if current_url and thumb_url and current_url != thumb_url:
        return False
    return True


def set_live_mode(user_id: str, seconds: float = LIVE_MODE_DEFAULT_SECONDS) -> float:
    """Turn on per-action thumbnail capture for `seconds`. Returns expiry ts."""
    expiry = time.time() + seconds
    _live_mode_until[user_id] = expiry
    return expiry


def clear_live_mode(user_id: str) -> None:
    _live_mode_until.pop(user_id, None)


def is_live_mode(user_id: str) -> bool:
    """True if live mode is enabled and not yet expired (auto-cleans on expiry)."""
    expiry = _live_mode_until.get(user_id)
    if expiry is None:
        return False
    if time.time() >= expiry:
        _live_mode_until.pop(user_id, None)
        return False
    return True


def live_mode_remaining(user_id: str) -> float:
    """Seconds left of live mode, or 0."""
    expiry = _live_mode_until.get(user_id)
    if expiry is None:
        return 0.0
    return max(0.0, expiry - time.time())


def clear_user(user_id: str) -> None:
    """Drop all state for a user (logout, session reset)."""
    _channels.pop(user_id, None)
    _latest_thumbs.pop(user_id, None)
    _live_mode_until.pop(user_id, None)
