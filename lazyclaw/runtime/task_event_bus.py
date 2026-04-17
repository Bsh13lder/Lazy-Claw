"""In-memory pub/sub for background-task completion events.

Mirrors the browser event bus pattern (`lazyclaw.browser.event_bus`) so that
when a background task finishes its result can be fan-in to any subscriber —
notably the web chat WebSocket — even though the WebSocketCallback that
originated the turn is long gone by the time the task completes.

Design constraints:
- Zero LLM token cost — frames never re-enter the agent context.
- Bounded memory — per-user ring buffer for initial paint on reconnect.
- Per-user isolation — events keyed on user_id, never cross-routed.
- Drop on backpressure — slow subscribers never block the task runner.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)

RING_SIZE = 20
SUBSCRIBER_QUEUE = 16


@dataclass(frozen=True)
class TaskEvent:
    """Immutable background-task lifecycle event."""

    user_id: str
    kind: str                     # background_done | background_failed
    task_id: str
    name: str
    ts: float = field(default_factory=time.time)
    result: str | None = None     # agent's final answer (truncated by producer)
    error: str | None = None
    duration_ms: int | None = None
    total_tokens: int | None = None
    llm_calls: int | None = None
    total_cost: float | None = None
    tools_used: tuple[str, ...] = ()

    def to_frame(self) -> dict:
        """JSON-safe WebSocket frame payload."""
        d = asdict(self)
        d["tools_used"] = list(self.tools_used)
        return {k: v for k, v in d.items() if v not in (None, ())}


class _UserChannel:
    __slots__ = ("ring", "subscribers")

    def __init__(self) -> None:
        self.ring: deque[TaskEvent] = deque(maxlen=RING_SIZE)
        self.subscribers: list[asyncio.Queue] = []


_channels: dict[str, _UserChannel] = {}
_lock = asyncio.Lock()


def _channel(user_id: str) -> _UserChannel:
    ch = _channels.get(user_id)
    if ch is None:
        ch = _UserChannel()
        _channels[user_id] = ch
    return ch


def publish(event: TaskEvent) -> None:
    """Publish a task event. Non-blocking; drops on subscriber backpressure."""
    if not event.user_id:
        return
    ch = _channel(event.user_id)
    ch.ring.append(event)
    for q in list(ch.subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.debug("Task event dropped for %s (slow subscriber)", event.user_id)


async def subscribe(user_id: str) -> AsyncIterator[TaskEvent]:
    """Async iterator over future task events for a user."""
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


def recent_events(
    user_id: str,
    limit: int = 5,
    max_age_s: float | None = 600.0,
) -> list[TaskEvent]:
    """Return the latest N events for initial paint on reconnect.

    Defaults to a 10-min window so stale completions don't mount as fresh
    banners after a long idle disconnect.
    """
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
