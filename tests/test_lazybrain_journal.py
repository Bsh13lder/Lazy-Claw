"""Journal date resolution — tests the cheap helpers, no DB required."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from lazyclaw.lazybrain import journal


def test_resolve_today_and_empty() -> None:
    today = date.today().isoformat()
    assert journal.resolve_date(None) == today
    assert journal.resolve_date("today") == today
    assert journal.resolve_date("TODAY") == today


def test_resolve_yesterday() -> None:
    expected = (date.today() - timedelta(days=1)).isoformat()
    assert journal.resolve_date("yesterday") == expected


def test_resolve_explicit_date() -> None:
    assert journal.resolve_date("2026-04-18") == "2026-04-18"


def test_resolve_invalid_date_raises() -> None:
    with pytest.raises(ValueError):
        journal.resolve_date("not-a-date")
    with pytest.raises(ValueError):
        journal.resolve_date("2026/04/18")  # wrong separator
