"""Cron expression parser and scheduler using croniter.

All cron expressions are interpreted in the **user's local timezone** (per
``lazyclaw.lazybrain.timezone_util.user_tz``). Storage is still UTC — we
just evaluate the expression in local time and convert the resulting
fire-time back to UTC. Without this, ``0 19 * * *`` ("7 PM") authored by
a Madrid user landed at 19:00 UTC = 21:00 Madrid in summer (UTC+2). The
2-hour offset matched the deployment tz exactly.

croniter ≥1.4 accepts a tz-aware datetime as its base and returns next-
fire times in the same tz, so the conversion is just ``base.astimezone()``
on the way out.
"""

from __future__ import annotations

from datetime import datetime, timezone

from lazyclaw.lazybrain.timezone_util import user_tz


def is_valid(expression: str) -> bool:
    """Check if a cron expression is valid without raising."""
    from croniter import croniter

    if not expression or not expression.strip():
        return False
    return croniter.is_valid(expression)


def parse_cron(expression: str, user_id: str | None = None):
    """Validate and parse a cron expression. Returns a croniter instance.

    Anchored to *now* in the user's local timezone so the very first
    ``get_next()`` call after construction reflects the user's wall clock.

    Raises ValueError if the expression is invalid.
    """
    from croniter import croniter

    if not expression or not expression.strip():
        raise ValueError("Empty cron expression")

    if not croniter.is_valid(expression):
        raise ValueError(
            f"Invalid cron expression: '{expression}'. "
            "Expected format: 'minute hour day month weekday' (e.g. '*/5 * * * *')"
        )

    return croniter(expression, datetime.now(user_tz(user_id)))


def get_next_run(
    expression: str,
    after: datetime | None = None,
    user_id: str | None = None,
) -> datetime:
    """Get the next run time after the given datetime.

    The cron expression is evaluated in the user's local tz. The returned
    datetime is always tz-aware in UTC — callers store ISO strings and the
    daemon's "is now past next_run?" comparison stays a UTC↔UTC compare.

    ``after`` (when provided) is converted to user-local time before being
    handed to croniter so a UTC ``last_run`` doesn't shift the cron grid.
    """
    from croniter import croniter

    if not croniter.is_valid(expression):
        raise ValueError(f"Invalid cron expression: '{expression}'")

    tz = user_tz(user_id)
    if after is None:
        base = datetime.now(tz)
    else:
        if after.tzinfo is None:
            after = after.replace(tzinfo=timezone.utc)
        base = after.astimezone(tz)

    cron = croniter(expression, base)
    next_dt = cron.get_next(datetime)

    if next_dt.tzinfo is None:
        # croniter ≥1.4 preserves tz; older builds don't — assume the
        # base's tz so we don't silently drift.
        next_dt = next_dt.replace(tzinfo=tz)

    # Always return UTC so the rest of the daemon can keep comparing
    # against datetime.now(timezone.utc).
    return next_dt.astimezone(timezone.utc)


def is_due(
    expression: str,
    last_run: str | None,
    next_run: str | None = None,
    user_id: str | None = None,
) -> bool:
    """Check if a cron job is due for execution.

    Args:
        expression: Cron expression string.
        last_run: ISO format datetime string of last run, or None.
        next_run: ISO format datetime string of pre-computed next fire (set
            at job-creation time). When ``last_run is None`` and ``next_run``
            is provided, this function returns True only when ``now >=
            next_run``. Without it, jobs created at 17:00 with cron
            "0 9,19 * * *" used to fire immediately on the next heartbeat
            tick because ``last_run is None`` short-circuited to True.
        user_id: Reserved for per-user tz overrides; passed through to
            :func:`get_next_run` so the cron grid is evaluated in the
            user's local timezone.

    Returns:
        True if the job should run now.
    """
    if last_run is None:
        if next_run:
            next_dt = datetime.fromisoformat(next_run)
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= next_dt
        # Legacy fallback — no scheduled next_run, fire on first tick.
        return True

    last_dt = datetime.fromisoformat(last_run)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    next_dt = get_next_run(expression, after=last_dt, user_id=user_id)
    return datetime.now(timezone.utc) >= next_dt


def calculate_next_run(expression: str, user_id: str | None = None) -> str:
    """Return the next run time as an ISO format string (UTC, tz-aware)."""
    return get_next_run(expression, user_id=user_id).isoformat()
