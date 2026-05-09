"""User-timezone helpers for the tasks module.

Single source of truth for "what wall-clock time is this user in?". Reads
``users.settings.general.timezone`` first, then falls back to the
``LAZYCLAW_DEFAULT_TZ`` env var, then to Europe/Madrid (the deploy
default), then to UTC if even Madrid fails to load.

Why a tasks-local helper rather than reusing ``lazybrain.timezone_util``:
that one is sync and env-only by design (its callers can't await). Tasks
need an async, per-user-aware lookup so a Telegram-only user with a
different tz from the Web user gets the right wall clock for their
reminders. Both helpers share the same fallback chain so they can't
disagree.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

_DEFAULT_TZ_NAME = "Europe/Madrid"


def _safe_zoneinfo(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _fallback_tz() -> ZoneInfo:
    env_tz = _safe_zoneinfo(os.environ.get("LAZYCLAW_DEFAULT_TZ"))
    if env_tz is not None:
        return env_tz
    madrid = _safe_zoneinfo(_DEFAULT_TZ_NAME)
    if madrid is not None:
        return madrid
    return ZoneInfo("UTC")


async def get_user_tz(config: Config, user_id: str | None) -> ZoneInfo:
    """Look up a user's timezone from settings, with safe fallbacks.

    Never raises — invalid or missing values fall back to env / Madrid /
    UTC in that order. Returns a ``ZoneInfo`` so callers can pass it
    straight to ``datetime.now(tz)``.
    """
    if not user_id:
        return _fallback_tz()
    try:
        from lazyclaw.settings.general import get_general_settings
        settings = await get_general_settings(config, user_id)
    except Exception:
        logger.debug("get_user_tz: settings lookup failed", exc_info=True)
        return _fallback_tz()
    tz = _safe_zoneinfo(settings.get("timezone"))
    if tz is not None:
        return tz
    return _fallback_tz()


def parse_user_dt(value: str | None, tz: ZoneInfo) -> datetime | None:
    """Parse an ISO-8601 string and return a timezone-aware datetime.

    If the string carries no offset, attach ``tz`` (the user's tz). If
    the string is None, empty, or unparseable, return None — callers
    treat None as "no value", never "now".
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def now_user(tz: ZoneInfo) -> datetime:
    """Current time in the user's timezone (tz-aware)."""
    return datetime.now(tz)


def to_utc_iso(dt: datetime) -> str:
    """Normalize any tz-aware datetime to UTC ISO-8601.

    Storage canonical form. Reading code uses ``parse_user_dt`` to bring
    it back into the user's local view.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
