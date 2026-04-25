"""User-timezone helper for LazyBrain date math.

Centralises the "what day is it for *this* user?" question so journal
auto-create, morning briefing, and any future daily-cadence feature all
agree on the boundary.

Resolution order:
  1. ``LAZYCLAW_DEFAULT_TZ`` env var (set in docker-compose.yml)
  2. Hard fallback: ``Europe/Madrid`` (the deploy this code runs on)
  3. UTC if even Madrid fails to load (zoneinfo db missing)

The ``user_id`` argument is reserved for a future per-user override; it
isn't used yet but is in the signature so callers don't need to change
when that lands.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_FALLBACK_TZ = "Europe/Madrid"


def user_tz(user_id: str | None = None) -> ZoneInfo:
    name = os.environ.get("LAZYCLAW_DEFAULT_TZ") or _FALLBACK_TZ
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        try:
            return ZoneInfo(_FALLBACK_TZ)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


def today_iso(user_id: str | None = None) -> str:
    return datetime.now(user_tz(user_id)).date().isoformat()


def yesterday_iso(user_id: str | None = None) -> str:
    return (datetime.now(user_tz(user_id)).date() - timedelta(days=1)).isoformat()
