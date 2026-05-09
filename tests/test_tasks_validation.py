"""Strict-validation tests for ``tasks.store`` write helpers.

Pinned bugs:
- ``pre_reminders`` accepted any list and only failed at heartbeat-tick
  time, silently stopping fire for the WHOLE list.
- ``reminder_at`` accepted any string and only blew up at recurring
  respawn time, silently dropping the offset.

These tests exercise the pure validation helpers without touching the
DB — the helpers are the line of defence that pushes failures from
"silent at runtime" to "loud at write-time".
"""
from __future__ import annotations

import pytest

from lazyclaw.tasks.store import (
    _compute_reminder_offset_minutes,
    _validate_iso_dt,
)


@pytest.mark.parametrize("good", [
    "2026-05-08T15:00:00",
    "2026-05-08T15:00:00+02:00",
    "2026-05-08T15:00:00.123456+00:00",
    None,
    "",
])
def test_validate_iso_dt_accepts_valid_or_empty(good) -> None:
    out = _validate_iso_dt(good, "reminder_at")
    if good in (None, ""):
        assert out is None
    else:
        assert out == good


@pytest.mark.parametrize("bad", [
    "tomorrow",
    "not a date",
    "2026/05/08",
    "2026-13-99T15:00:00",
])
def test_validate_iso_dt_rejects_garbage(bad) -> None:
    with pytest.raises(ValueError):
        _validate_iso_dt(bad, "reminder_at")


def test_validate_iso_dt_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        _validate_iso_dt(12345, "reminder_at")  # type: ignore[arg-type]


def test_validate_iso_dt_strips_whitespace() -> None:
    out = _validate_iso_dt("  2026-05-08T15:00:00  ", "reminder_at")
    assert out == "2026-05-08T15:00:00"


# ── reminder offset minutes ────────────────────────────────────────────


def test_offset_minutes_basic_60min() -> None:
    """Reminder 1h before due → 60 minute offset."""
    offset = _compute_reminder_offset_minutes(
        due_date="2026-05-08T10:00:00+00:00",
        reminder_at="2026-05-08T09:00:00+00:00",
    )
    assert offset == -60


def test_offset_minutes_due_date_as_bare_date() -> None:
    """due_date may be YYYY-MM-DD; expand to midnight UTC."""
    offset = _compute_reminder_offset_minutes(
        due_date="2026-05-08",
        reminder_at="2026-05-07T22:00:00+00:00",
    )
    # 22:00 the day before midnight start = -120 minutes
    assert offset == -120


@pytest.mark.parametrize("a, b", [
    (None, "2026-05-08T09:00:00"),
    ("2026-05-08", None),
    (None, None),
    ("not-a-date", "2026-05-08T09:00:00"),
])
def test_offset_minutes_returns_none_when_unparseable(a, b) -> None:
    assert _compute_reminder_offset_minutes(a, b) is None
