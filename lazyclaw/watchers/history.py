"""In-memory watcher check history — per (user, watcher) ring buffer.

Every poll the heartbeat daemon runs gets recorded here via `record_check`.
The watchers REST router + NL skills read back via `get_history` and
`get_stats`. Zero DB writes — lost on restart, which is fine for last-N
debug visibility. Perfect audit trail (what fired, when, why didn't this
one fire) would warrant a real table, but that's a separate phase.

Pattern mirrors lazyclaw/browser/event_bus.py — bounded memory, per-user
isolation, no cross-tenant bleed.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Iterable

# Last N checks kept per watcher. 20 is plenty for UI — a 5-min interval
# gives ~100 min of retroactive visibility, a 30s interval gives 10 min.
RING_SIZE = 20


@dataclass(frozen=True)
class WatcherCheck:
    """Immutable record of one watcher poll."""

    ts: float = field(default_factory=time.time)
    changed: bool = False           # did the extracted value change from last time?
    triggered: bool = False         # did we push a notification this check?
    value_preview: str | None = None  # extracted value (truncated)
    error: str | None = None        # "timeout", "JS error: ...", etc.
    notification: str | None = None  # the text we sent to Telegram, if any

    def to_dict(self) -> dict:
        return asdict(self)


# { user_id: { watcher_id: deque[WatcherCheck] } }
_rings: dict[str, dict[str, deque[WatcherCheck]]] = {}


def _ring(user_id: str, watcher_id: str) -> deque[WatcherCheck]:
    user_bucket = _rings.setdefault(user_id, {})
    ring = user_bucket.get(watcher_id)
    if ring is None:
        ring = deque(maxlen=RING_SIZE)
        user_bucket[watcher_id] = ring
    return ring


def record_check(
    user_id: str,
    watcher_id: str,
    *,
    changed: bool = False,
    triggered: bool = False,
    value_preview: str | None = None,
    error: str | None = None,
    notification: str | None = None,
) -> None:
    """Append a check record for a watcher. Non-blocking, bounded."""
    if not user_id or not watcher_id:
        return
    if value_preview is not None and len(value_preview) > 500:
        value_preview = value_preview[:500] + "…"
    if notification is not None and len(notification) > 400:
        notification = notification[:400] + "…"
    _ring(user_id, watcher_id).append(
        WatcherCheck(
            changed=changed,
            triggered=triggered,
            value_preview=value_preview,
            error=error,
            notification=notification,
        )
    )


def get_history(
    user_id: str, watcher_id: str, limit: int = RING_SIZE,
) -> list[WatcherCheck]:
    """Return latest checks (newest-last). Empty list if nothing recorded."""
    ring = _rings.get(user_id, {}).get(watcher_id)
    if not ring:
        return []
    items = list(ring)
    if limit and limit < len(items):
        return items[-limit:]
    return items


def get_stats(user_id: str, watcher_id: str) -> dict:
    """Aggregate counters derived from the ring. Best-effort — doesn't
    include checks from before the last process restart."""
    items = get_history(user_id, watcher_id)
    check_count = len(items)
    trigger_count = sum(1 for c in items if c.triggered)
    error_count = sum(1 for c in items if c.error)
    last = items[-1] if items else None
    last_trigger = next(
        (c for c in reversed(items) if c.triggered), None,
    )
    return {
        "check_count": check_count,
        "trigger_count": trigger_count,
        "error_count": error_count,
        "last_check_ts": last.ts if last else None,
        "last_value_preview": last.value_preview if last else None,
        "last_error": last.error if last else None,
        "last_trigger_ts": last_trigger.ts if last_trigger else None,
        "last_trigger_message": last_trigger.notification if last_trigger else None,
    }


def forget_watcher(user_id: str, watcher_id: str) -> None:
    """Drop history for a deleted watcher."""
    bucket = _rings.get(user_id)
    if bucket and watcher_id in bucket:
        del bucket[watcher_id]


def forget_user(user_id: str) -> None:
    """Drop all watcher history for a user (logout, account deletion)."""
    _rings.pop(user_id, None)


def snapshot(user_id: str) -> dict[str, list[WatcherCheck]]:
    """All watchers' latest rings for a user — useful for bulk endpoints."""
    bucket = _rings.get(user_id) or {}
    return {wid: list(ring) for wid, ring in bucket.items()}


def _all_users() -> Iterable[str]:
    return list(_rings.keys())
