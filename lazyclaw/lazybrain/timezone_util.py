"""User-timezone helper for LazyBrain date math.

Centralises the "what day is it for *this* user?" question so journal
auto-create, morning briefing, and any future daily-cadence feature all
agree on the boundary.

Resolution order:
  1. The per-user ``settings.general.timezone`` — via the write-through
     cache below (this helper is sync; it cannot await the settings read
     itself, so ``tasks.timezone.get_user_tz`` and the settings-save path
     publish into the cache with :func:`remember_user_tz`)
  2. ``LAZYCLAW_DEFAULT_TZ`` env var (set in docker-compose.yml)
  3. Hard fallback: ``Europe/Madrid`` (the deploy this code runs on)
  4. UTC if even Madrid fails to load (zoneinfo db missing)

Until 2026-07-30 this helper was env-only while every DISPLAY path used the
settings-aware ``tasks.timezone.get_user_tz`` — so the moment a user set a
non-Madrid settings timezone, reminders/crons/respawns kept firing on the
Madrid grid while all shown times used the new zone. The cache closes that
split for this single-process deployment; before the first settings read of
a process the env fallback still applies (same value in the shipped
docker-compose).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_FALLBACK_TZ = "Europe/Madrid"

# user_id -> IANA zone name from users.settings.general.timezone. Kept fresh
# by tasks.timezone.get_user_tz (every successful settings read) and by the
# settings PATCH path. Names are validated by the publisher.
_SETTINGS_TZ: dict[str, str] = {}


def remember_user_tz(user_id: str | None, name: str | None) -> None:
    """Publish a user's settings timezone into the sync-lookup cache.

    Invalid/empty names clear the entry so a bad save can't wedge firing
    math on a stale zone.
    """
    if not user_id:
        return
    if not name:
        _SETTINGS_TZ.pop(user_id, None)
        return
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        _SETTINGS_TZ.pop(user_id, None)
        return
    _SETTINGS_TZ[user_id] = name


def user_tz(user_id: str | None = None) -> ZoneInfo:
    cached = _SETTINGS_TZ.get(user_id) if user_id else None
    name = cached or os.environ.get("LAZYCLAW_DEFAULT_TZ") or _FALLBACK_TZ
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
