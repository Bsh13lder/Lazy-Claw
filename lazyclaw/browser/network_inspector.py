"""In-memory per-user capture of CDP Network.* events.

Exposes a bounded ring buffer of request metadata. Response bodies are NOT
fetched eagerly — the ``network`` action handler calls
``Network.getResponseBody`` lazily at query time only, so a page that makes
1000+ fetches doesn't generate 1000+ CDP round-trips (and associated memory)
when the agent never asks about them.

Thread-safety: all writes happen on the CDP listener coroutine, all reads
happen on the action handler coroutine — both single-threaded under asyncio.
No locking required.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Optional

logger = logging.getLogger(__name__)

# Keep the last N requests per user. Tuned for typical SPA page loads (~50
# fetches) plus a handful of user-triggered actions.
_MAX_RECORDS_PER_USER = 100


@dataclass(frozen=True)
class NetRecord:
    """One request/response pair as observed by CDP."""

    request_id: str
    url: str
    method: str
    request_ts: float
    status: Optional[int] = None
    mime_type: Optional[str] = None
    response_ts: Optional[float] = None
    response_size: Optional[int] = None
    from_cache: bool = False
    failed: bool = False
    error_text: Optional[str] = None


# Module-level state: per-user deque + index-by-request-id for O(1) updates.
_buffers: dict[str, deque[NetRecord]] = {}
_index: dict[str, dict[str, NetRecord]] = {}  # user_id -> request_id -> record


def _buf(user_id: str) -> deque[NetRecord]:
    buf = _buffers.get(user_id)
    if buf is None:
        buf = deque(maxlen=_MAX_RECORDS_PER_USER)
        _buffers[user_id] = buf
        _index[user_id] = {}
    return buf


def _replace_record(user_id: str, old: NetRecord, new: NetRecord) -> None:
    """Swap a record in-place in the deque (preserves order)."""
    buf = _buffers.get(user_id)
    if buf is None:
        return
    try:
        idx = next(i for i, r in enumerate(buf) if r.request_id == old.request_id)
    except StopIteration:
        return
    buf[idx] = new
    _index[user_id][new.request_id] = new


def record_request(user_id: str | None, request_id: str, url: str, method: str) -> None:
    """Handle Network.requestWillBeSent."""
    if not user_id or not request_id:
        return
    rec = NetRecord(
        request_id=request_id,
        url=url,
        method=method,
        request_ts=time.time(),
    )
    buf = _buf(user_id)
    # If deque is full the oldest record gets evicted — also drop it from the index.
    if len(buf) == buf.maxlen:
        oldest = buf[0]
        _index[user_id].pop(oldest.request_id, None)
    buf.append(rec)
    _index[user_id][request_id] = rec


def record_response(
    user_id: str | None,
    request_id: str,
    status: int,
    mime_type: str | None,
    response_size: int | None = None,
    from_cache: bool = False,
) -> None:
    """Handle Network.responseReceived."""
    if not user_id or not request_id:
        return
    existing = _index.get(user_id, {}).get(request_id)
    if existing is None:
        return
    updated = replace(
        existing,
        status=status,
        mime_type=mime_type,
        response_size=response_size,
        from_cache=from_cache,
    )
    _replace_record(user_id, existing, updated)


def record_finished(
    user_id: str | None,
    request_id: str,
    encoded_data_length: float | None = None,
) -> None:
    """Handle Network.loadingFinished."""
    if not user_id or not request_id:
        return
    existing = _index.get(user_id, {}).get(request_id)
    if existing is None:
        return
    size = int(encoded_data_length) if encoded_data_length is not None else existing.response_size
    updated = replace(
        existing,
        response_ts=time.time(),
        response_size=size,
    )
    _replace_record(user_id, existing, updated)


def record_failed(user_id: str | None, request_id: str, error_text: str | None) -> None:
    """Handle Network.loadingFailed."""
    if not user_id or not request_id:
        return
    existing = _index.get(user_id, {}).get(request_id)
    if existing is None:
        return
    updated = replace(
        existing,
        failed=True,
        error_text=error_text,
        response_ts=time.time(),
    )
    _replace_record(user_id, existing, updated)


def query(
    user_id: str | None,
    *,
    url_substring: str | None = None,
    method: str | None = None,
    status_min: int | None = None,
    status_max: int | None = None,
    since_ts: float | None = None,
    only_failed: bool = False,
    limit: int = 20,
) -> tuple[list[NetRecord], bool, int]:
    """Return filtered records.

    Returns (records, truncated, total_seen) where ``truncated`` means the
    ring buffer filled and older requests were evicted before being seen.
    ``total_seen`` is the number of matches before ``limit`` was applied.
    """
    if not user_id:
        return [], False, 0
    buf = _buffers.get(user_id)
    if not buf:
        return [], False, 0

    method_upper = method.upper() if method else None
    matches: list[NetRecord] = []
    for rec in buf:
        if url_substring and url_substring.lower() not in rec.url.lower():
            continue
        if method_upper and rec.method.upper() != method_upper:
            continue
        if status_min is not None and (rec.status is None or rec.status < status_min):
            continue
        if status_max is not None and (rec.status is None or rec.status > status_max):
            continue
        if since_ts is not None and rec.request_ts < since_ts:
            continue
        if only_failed and not rec.failed:
            continue
        matches.append(rec)

    total_seen = len(matches)
    truncated = len(buf) == buf.maxlen
    # Newest first — agents overwhelmingly care about "what just happened".
    matches.reverse()
    return matches[: max(1, limit)], truncated, total_seen


def clear(user_id: str | None) -> None:
    """Drop all records for a user. Called on browser close / logout."""
    if not user_id:
        return
    _buffers.pop(user_id, None)
    _index.pop(user_id, None)
