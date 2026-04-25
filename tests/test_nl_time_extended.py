"""Coverage for the natural-language phrases the user typically types
that the original parser missed: ``within N days``, ``N day(s) deadline``,
``by Friday``. Pin the new behaviour so regressions show up loud.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from lazyclaw.tasks import nl_time


def _today() -> str:
    return nl_time._today_local().isoformat()


def _plus_days(n: int) -> str:
    return (nl_time._today_local() + timedelta(days=n)).isoformat()


@pytest.mark.parametrize("phrase", [
    "within 2 days buy organic eggs",
    "within 2 days",
    "WITHIN 2 days fix the bug",
])
def test_within_n_days_resolves_to_today_plus_n(phrase: str) -> None:
    parsed = nl_time.parse(phrase)
    assert parsed.due_date == _plus_days(2), f"{phrase!r} should land 2 days out"
    assert parsed.reminder_at is not None


@pytest.mark.parametrize("phrase,expected_days", [
    ("2 days deadline pay rent", 2),
    ("3 day deadline finish migration", 3),
    ("1 day deadline call mom", 1),
    ("2 días límite enviar factura", 2),
])
def test_n_unit_deadline(phrase: str, expected_days: int) -> None:
    parsed = nl_time.parse(phrase)
    assert parsed.due_date == _plus_days(expected_days), (
        f"{phrase!r} should resolve to today+{expected_days} days"
    )
    # Title should NOT contain the deadline phrase (parser strips it).
    assert "deadline" not in (parsed.remaining or "").lower()
    assert "límite" not in (parsed.remaining or "").lower()


@pytest.mark.parametrize("phrase", [
    "by Friday submit invoice",
    "before Friday email Anna",
    "antes del viernes pagar luz",
])
def test_by_weekday(phrase: str) -> None:
    parsed = nl_time.parse(phrase)
    assert parsed.due_date is not None
    parsed_dt = datetime.fromisoformat(parsed.due_date)
    # Friday is weekday 4
    assert parsed_dt.weekday() == 4, (
        f"{phrase!r} should resolve to a Friday, got {parsed_dt}"
    )


def test_within_does_not_clobber_in_duration() -> None:
    # The original "in 2 days" path should keep working unchanged.
    parsed = nl_time.parse("in 2 days workshop")
    assert parsed.due_date == _plus_days(2)


def test_n_unit_deadline_beats_absolute_date_for_bare_number() -> None:
    # "2 days deadline" must NOT be re-interpreted as "the 2nd of this month
    # at 09:00" by the dateutil fallback.
    parsed = nl_time.parse("2 days deadline file taxes")
    today = nl_time._today_local()
    assert parsed.due_date == (today + timedelta(days=2)).isoformat()
    assert "file taxes" in (parsed.remaining or "")


def test_full_pipeline_within_plus_priority_plus_tag() -> None:
    out = nl_time.parse_full("within 2 days buy organic eggs urgent #shopping")
    assert out["due_date"] == _plus_days(2)
    assert out["priority"] == "urgent"
    assert "shopping" in out["tags"]
    assert "buy organic eggs" in out["title"]
