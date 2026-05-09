"""Timezone-correctness tests for the tasks module.

Pinned bugs:
- ``smart_intake._validate_deadline`` used to attach UTC when the LLM
  omitted the offset, dropping inferred deadlines 1–2h off for users in
  non-UTC zones (the original Madrid bug).
- ``nl_time.parse`` was hardcoded to Madrid via a module-level
  ``_LOCAL_TZ``; per-user override couldn't flow through.
- ``parse_user_dt`` is the single helper meant to replace ad-hoc
  ``datetime.fromisoformat()`` + UTC-fallback patterns scattered through
  store.py.

These tests don't touch the DB — they exercise the pure helpers so a
regression shows up before the daemon ever sees a malformed value.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from lazyclaw.tasks import nl_time, smart_intake, timezone as tasks_tz


# ── tasks.timezone helpers ─────────────────────────────────────────────


def test_parse_user_dt_attaches_user_tz_when_missing() -> None:
    madrid = ZoneInfo("Europe/Madrid")
    parsed = tasks_tz.parse_user_dt("2026-05-08T15:00:00", madrid)
    assert parsed is not None
    assert parsed.tzinfo == madrid


def test_parse_user_dt_preserves_existing_offset() -> None:
    madrid = ZoneInfo("Europe/Madrid")
    parsed = tasks_tz.parse_user_dt("2026-05-08T15:00:00+02:00", madrid)
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "2026-13-99T15:00"])
def test_parse_user_dt_rejects_garbage(bad) -> None:
    madrid = ZoneInfo("Europe/Madrid")
    assert tasks_tz.parse_user_dt(bad, madrid) is None


def test_to_utc_iso_normalizes_tz_aware_input() -> None:
    madrid = ZoneInfo("Europe/Madrid")
    dt = datetime(2026, 5, 8, 15, 0, 0, tzinfo=madrid)
    iso = tasks_tz.to_utc_iso(dt)
    # Madrid in May is UTC+2, so 15:00 Madrid = 13:00 UTC.
    assert iso.startswith("2026-05-08T13:00:00")
    assert iso.endswith("+00:00")


# ── smart_intake._validate_deadline ────────────────────────────────────


def test_validate_deadline_attaches_user_tz_not_utc() -> None:
    """Naive ISO strings should attach the *user's* tz, not UTC."""
    madrid = ZoneInfo("Europe/Madrid")
    # Future date so the past-deadline rejection doesn't fire.
    future = (datetime.now(madrid) + timedelta(days=2)).strftime(
        "%Y-%m-%dT15:00:00",
    )
    out = smart_intake._validate_deadline(future, madrid)
    assert out is not None
    parsed = datetime.fromisoformat(out)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == madrid.utcoffset(parsed)


def test_validate_deadline_legacy_no_tz_arg_keeps_utc_default() -> None:
    """Legacy callers (no user_tz arg) still get UTC for back-compat."""
    future = (
        datetime.now(timezone.utc) + timedelta(days=2)
    ).strftime("%Y-%m-%dT15:00:00")
    out = smart_intake._validate_deadline(future)
    assert out is not None
    parsed = datetime.fromisoformat(out)
    assert parsed.utcoffset() == timedelta(0)


# ── nl_time tz parameter ───────────────────────────────────────────────


def test_nl_time_parse_uses_supplied_tz_for_tomorrow() -> None:
    """`tomorrow 9am` should land at 9am in the user's tz, not server's."""
    nyc = ZoneInfo("America/New_York")
    parsed = nl_time.parse("tomorrow at 9am buy milk", tz=nyc)
    assert parsed.due_date is not None
    assert parsed.reminder_at is not None
    rem = datetime.fromisoformat(parsed.reminder_at)
    # Stored as UTC; converting back to NYC must give 09:00.
    nyc_local = rem.astimezone(nyc)
    assert nyc_local.hour == 9
    assert nyc_local.minute == 0


def test_nl_time_parse_default_tz_is_madrid() -> None:
    """No tz arg → Madrid default (the deploy fallback)."""
    parsed = nl_time.parse("tomorrow at 10am")
    rem = datetime.fromisoformat(parsed.reminder_at)
    madrid_local = rem.astimezone(ZoneInfo("Europe/Madrid"))
    assert madrid_local.hour == 10


def test_today_local_back_compat_no_args() -> None:
    """Existing tests call ``nl_time._today_local()`` with no args; that
    must still work (Madrid default) so we don't break the pinned suite."""
    today = nl_time._today_local()
    assert today is not None
