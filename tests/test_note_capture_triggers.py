"""Trigger-phrase detection for the Telegram quick-note capture path."""
from __future__ import annotations

import pytest

from lazyclaw.channels.note_capture import detect_trigger, kind_label


@pytest.mark.parametrize("phrase,expected_kind,expected_content", [
    ("note: buy organic eggs", "kind/note", "buy organic eggs"),
    ("Note: SUMMER PROMO IDEAS", "kind/note", "SUMMER PROMO IDEAS"),
    ("idea: agent should ask before deleting",
        "kind/idea", "agent should ask before deleting"),
    ("remember: mom's BP 120/80",
        "kind/memory", "mom's BP 120/80"),
    ("memo: standup at 10",
        "kind/note", "standup at 10"),
    ("nota: comprar leche",
        "kind/note", "comprar leche"),
    ("recuerda: cumpleaños 14 mayo",
        "kind/memory", "cumpleaños 14 mayo"),
    # Tolerate extra spaces and dash separators
    ("note  :   trailing spaces still ok", "kind/note", "trailing spaces still ok"),
    ("idea - dash separator works", "kind/idea", "dash separator works"),
])
def test_trigger_matches_expected(
    phrase: str, expected_kind: str, expected_content: str,
) -> None:
    result = detect_trigger(phrase)
    assert result is not None, f"{phrase!r} should match"
    kind, content = result
    assert kind == expected_kind
    assert content == expected_content


@pytest.mark.parametrize("phrase", [
    "no trigger word here",
    "this isn't a note: sentence with note in middle",
    "note:",          # empty body
    "note: ",         # whitespace only
    "",
    "remember to call mom",  # no colon → falls through
])
def test_non_triggers_pass_through(phrase: str) -> None:
    assert detect_trigger(phrase) is None


def test_kind_label_is_human_friendly() -> None:
    assert kind_label("kind/note") == "Note"
    assert kind_label("kind/idea") == "Idea"
    assert kind_label("kind/memory") == "Memory"
    # Unknown kinds still return something usable, not a crash.
    assert kind_label("kind/random") == "Note"


def test_multiline_content_is_preserved() -> None:
    phrase = "note: line 1\nline 2\nline 3"
    result = detect_trigger(phrase)
    assert result is not None
    _, content = result
    assert content == "line 1\nline 2\nline 3"
