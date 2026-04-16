"""Cron expression parser and scheduler using croniter."""

from __future__ import annotations

from datetime import datetime, timezone


def is_valid(expression: str) -> bool:
    """Check if a cron expression is valid without raising."""
    from croniter import croniter

    if not expression or not expression.strip():
        return False
    return croniter.is_valid(expression)


def parse_cron(expression: str):
    """Validate and parse a cron expression. Returns a croniter instance.

    Raises ValueError if the expression is invalid.
    """
    from croniter import croniter

    if not expression or not expression.strip():
        raise ValueError(f"Empty cron expression")

    if not croniter.is_valid(expression):
        raise ValueError(
            f"Invalid cron expression: '{expression}'. "
            "Expected format: 'minute hour day month weekday' (e.g. '*/5 * * * *')"
        )

    return croniter(expression, datetime.now(timezone.utc))


def get_next_run(expression: str, after: datetime | None = None) -> datetime:
    """Get the next run time after the given datetime (default: now UTC)."""
    from croniter import croniter

    base = after if after is not None else datetime.now(timezone.utc)

    if not croniter.is_valid(expression):
        raise ValueError(f"Invalid cron expression: '{expression}'")

    cron = croniter(expression, base)
    next_dt = cron.get_next(datetime)

    if next_dt.tzinfo is None:
        return next_dt.replace(tzinfo=timezone.utc)
    return next_dt


def is_due(expression: str, last_run: str | None) -> bool:
    """Check if a cron job is due for execution.

    Args:
        expression: Cron expression string.
        last_run: ISO format datetime string of last run, or None.

    Returns:
        True if the job should run now.
    """
    if last_run is None:
        return True

    last_dt = datetime.fromisoformat(last_run)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    next_dt = get_next_run(expression, after=last_dt)
    return datetime.now(timezone.utc) >= next_dt


def calculate_next_run(expression: str) -> str:
    """Return the next run time as an ISO format string."""
    return get_next_run(expression).isoformat()
