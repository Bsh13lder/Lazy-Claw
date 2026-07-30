"""Advance-reminder (``pre_reminders``) resolution.

Given a task's absolute reminder time and the user's configured
``reminder_offsets`` (e.g. ``["-2h", "-1h"]``), compute the absolute
ISO-8601 timestamps at which advance reminders should fire — the
Proton-Calendar "remind me 2h and 1h before" pattern.

Extracted from ``skills.builtin.task_manager`` so BOTH the chat/Telegram
skill path AND the REST/mobile ``POST /api/tasks`` route derive the same
advance reminders. This module ONLY computes the list; the heartbeat daemon
(``daemon._fire_due_pre_reminders``) is what actually fires each entry.

Design: offsets apply to EVERY timed task (any task with a ``reminder_at``),
not only "important" ones — the user opts in by configuring offsets, and can
opt a single task out by passing ``explicit=[]``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

# Fallback offsets used only when the settings read fails. Mirrors
# ``settings.general.DEFAULT_GENERAL["reminder_offsets"]``.
# One advance ping, not two — trimmed 2026-07-30 as part of the noise pass:
# a single timed task used to generate 2 pre-reminders + 5 nags + 3 local
# alarms + feed pops (~14 pings/day). The user can widen reminder_offsets in
# Settings; this is only the nothing-configured fallback.
_FALLBACK_OFFSETS: tuple[str, ...] = ("-30m",)

# Negative relative offset like "-2h", "-30m", "-1d2h30m".
_OFFSET_RE = re.compile(
    r"^-\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?$",
    re.IGNORECASE,
)


def parse_offset_against(value: str, base: datetime) -> datetime | None:
    """Parse a negative relative offset like ``-2h`` as ``base - offset``.

    Returns ``None`` when ``value`` isn't a valid non-zero offset.
    """
    if not value:
        return None
    match = _OFFSET_RE.match(value.strip())
    if not match:
        return None
    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    if days == 0 and hours == 0 and minutes == 0:
        return None
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base - timedelta(days=days, hours=hours, minutes=minutes)


def parse_iso_datetime(value: str) -> datetime | None:
    """Parse an ISO-8601 datetime string → UTC-aware datetime, or ``None``.

    An aware input is converted to UTC (the docstring always promised this;
    until 2026-07-30 a ``+02:00`` value kept its offset and every timestamp
    derived from it inherited the same 2h skew against the daemon's lexical
    string compare). A naive input is still read as UTC — by the time values
    reach this module they are canonical UTC-aware (``_normalize_reminder_to_utc``
    runs at every write boundary); naive-as-UTC only remains as the least-wrong
    reading for un-migrated legacy strings.
    """
    try:
        dt = datetime.fromisoformat(value.strip())
    except (ValueError, TypeError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _load_offsets(config: Config, user_id: str) -> list[str]:
    """Read the user's configured ``reminder_offsets``.

    An explicitly empty list is honoured (global opt-out → no advance
    reminders). The ``_FALLBACK_OFFSETS`` only kick in when the value is
    genuinely absent (``None``) or the settings read raises.
    """
    try:
        from lazyclaw.settings.general import get_general_settings

        gen = await get_general_settings(config, user_id)
        offsets = gen.get("reminder_offsets")
        if offsets is None:
            return list(_FALLBACK_OFFSETS)
        return list(offsets)
    except Exception:
        logger.debug("Failed to read reminder_offsets; using fallback", exc_info=True)
        return list(_FALLBACK_OFFSETS)


def _timed_due_instant(due_date: str | None, user_id: str) -> datetime | None:
    """The UTC instant of a TIMED due date, or None for date-only/absent.

    A naive timed due is the user's local wall-clock (mobile writes
    ``YYYY-MM-DDTHH:MM:SS``); an aware one converts directly.
    """
    if not due_date or "T" not in due_date:
        return None
    try:
        dt = datetime.fromisoformat(due_date.strip())
    except (ValueError, TypeError, AttributeError):
        return None
    if dt.tzinfo is None:
        from lazyclaw.lazybrain.timezone_util import user_tz

        dt = dt.replace(tzinfo=user_tz(user_id))
    return dt.astimezone(timezone.utc)


async def resolve_pre_reminders(
    config: Config,
    user_id: str,
    *,
    reminder_at: str | None,
    due_date: str | None = None,
    explicit: list[str] | None = None,
) -> list[str]:
    """Compute advance-reminder ISO timestamps for a task.

    - ``explicit`` (when not ``None``) wins verbatim: pass ``[]`` to opt this
      task out, or a list of offsets / absolute ISO datetimes to override.
    - Otherwise offsets come from the user's ``reminder_offsets`` setting and
      apply to EVERY task with a ``reminder_at`` (Proton-Calendar style).

    Offsets are anchored on the EVENT: a TIMED ``due_date`` when present,
    else ``reminder_at``. The phone's local alarms use exactly this
    precedence (``reminderBaseTime`` in the mobile app), so both surfaces
    now ping at the SAME instants — anchoring on ``reminder_at`` while the
    phone anchored on the due time made the two ladders interleave into
    seemingly random pings ("-2h" firing at 05:30 while the phone rang at
    06:00).

    Returns a sorted, de-duplicated list of absolute ISO-8601 timestamps that
    fall strictly between "now" and the anchor, excluding the reminder
    instant itself (the at-time reminder is the nag ladder's entry 0 — a
    pre-reminder landing on the same minute would double-ping). Returns
    ``[]`` when there is no reminder time, no offsets, or nothing resolves
    to a future-but-pre-due instant. Never mutates its inputs.
    """
    if not reminder_at:
        return []
    reminder_instant = parse_iso_datetime(reminder_at)
    if reminder_instant is None:
        return []
    base = _timed_due_instant(due_date, user_id) or reminder_instant

    raw = explicit if explicit is not None else await _load_offsets(config, user_id)
    if not raw:
        return []

    now = datetime.now(timezone.utc)
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        dt = parse_offset_against(entry, base) or parse_iso_datetime(entry)
        if dt and now < dt < base and dt != reminder_instant:
            out.append(dt.isoformat())
    resolved = sorted(set(out))
    logger.debug(
        "[tasks] pre_reminders resolved user=%s offsets=%d resolved=%d",
        user_id, len(raw), len(resolved),
    )
    return resolved
