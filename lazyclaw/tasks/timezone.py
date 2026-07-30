"""User-timezone helpers for the tasks module.

Single source of truth for "what wall-clock time is this user in?". Reads
``users.settings.general.timezone`` first, then falls back to the
``LAZYCLAW_DEFAULT_TZ`` env var, then to Europe/Madrid (the deploy
default), then to UTC if even Madrid fails to load.

Why a tasks-local helper rather than reusing ``lazybrain.timezone_util``:
that one is sync (its callers can't await). Tasks need an async,
per-user-aware lookup so a Telegram-only user with a different tz from the
Web user gets the right wall clock for their reminders. Every successful
settings read here is published into ``timezone_util``'s write-through
cache (``remember_user_tz``), so the sync firing-math paths resolve the
SAME zone as the display paths instead of silently staying on the env
default when the user changes their settings timezone.
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
    name = settings.get("timezone")
    tz = _safe_zoneinfo(name)
    if tz is not None:
        try:
            from lazyclaw.lazybrain.timezone_util import remember_user_tz
            remember_user_tz(user_id, name)
        except Exception:
            logger.debug("get_user_tz: tz cache publish failed", exc_info=True)
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
