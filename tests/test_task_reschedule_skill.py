"""NL parsing for the new ``RescheduleTaskSkill``.

These tests pin the regex parsers so a regression in phrase coverage
shows up loud — they don't drive the full skill (that needs DB +
encryption), only the phrase → action extraction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lazyclaw.skills.builtin import task_reschedule


# ── Snooze parser ──────────────────────────────────────────────────────


@pytest.mark.parametrize("phrase, expected_minutes", [
    ("snooze 30 minutes", 30),
    ("snooze 30m", 30),
    ("snooze 2 hours", 120),
    ("snooze 2h", 120),
    ("snooze 1 day", 24 * 60),
    ("delay 15m", 15),
    ("aplaza 30 minutos", 30),
    ("posponer 1 hora", 60),
])
def test_parse_snooze_returns_future_dt(
    phrase: str, expected_minutes: int,
) -> None:
    out = task_reschedule._parse_snooze(phrase)
    assert out is not None
    delta = out - datetime.now(timezone.utc)
    # Allow 5s slack for clock movement during the test.
    assert abs(delta.total_seconds() - expected_minutes * 60) < 5


@pytest.mark.parametrize("phrase", [
    "push to Friday 10am",
    "tomorrow at 9",
    "clear deadline",
    "set priority high",
    "",
])
def test_parse_snooze_returns_none_for_non_snooze(phrase: str) -> None:
    assert task_reschedule._parse_snooze(phrase) is None


# ── Priority phrase regex ──────────────────────────────────────────────


@pytest.mark.parametrize("phrase, expected", [
    ("set priority high", "high"),
    ("priority urgent", "urgent"),
    ("priority urgente", "urgent"),
    ("priority alta", "high"),
    ("priority baja", "low"),
])
def test_priority_phrase_extracts_normalized(
    phrase: str, expected: str,
) -> None:
    m = task_reschedule._PRIORITY_PHRASE_RE.search(phrase)
    assert m is not None
    word = m.group(1).lower()
    assert task_reschedule._PRIORITY_MAP[word] == expected


# ── Clear-deadline phrase regex ────────────────────────────────────────


@pytest.mark.parametrize("phrase", [
    "clear deadline",
    "remove the deadline",
    "wipe the due date",
    "elimina el recordatorio",
    "borra la fecha",
])
def test_clear_deadline_phrase_matches(phrase: str) -> None:
    assert task_reschedule._CLEAR_DEADLINE_RE.search(phrase) is not None


@pytest.mark.parametrize("phrase", [
    "snooze 2h",
    "push to Friday 10am",
    "set priority high",
    "what's the deadline?",
])
def test_clear_deadline_phrase_doesnt_match_unrelated(phrase: str) -> None:
    assert task_reschedule._CLEAR_DEADLINE_RE.search(phrase) is None
