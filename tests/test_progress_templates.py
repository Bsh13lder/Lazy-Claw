"""Pure helper tests for progress_templates module.

DB-touching CRUD is exercised in integration tests; here we pin the
input normalizers + cron validator so format regressions surface
loud at write time.
"""
from __future__ import annotations

import pytest

from lazyclaw.tasks import progress_templates as pt


# ── Question normalizer ────────────────────────────────────────────────


def test_normalize_questions_accepts_strings() -> None:
    out = pt._normalize_questions(["Where are you?", "Any blocker?"])
    assert len(out) == 2
    assert all(q["kind"] == "progress" for q in out)
    assert out[0]["label"] == "Where are you?"


def test_normalize_questions_preserves_dict_kind() -> None:
    out = pt._normalize_questions([
        {"label": "ETA?", "kind": "eta"},
        {"label": "Blocker?", "kind": "blocker"},
    ])
    assert out[0]["kind"] == "eta"
    assert out[1]["kind"] == "blocker"


def test_normalize_questions_drops_invalid_kind() -> None:
    out = pt._normalize_questions([{"label": "X", "kind": "garbage"}])
    assert out[0]["kind"] == "progress"


def test_normalize_questions_drops_empty_labels() -> None:
    out = pt._normalize_questions(["", {"label": ""}, "good"])
    assert len(out) == 1
    assert out[0]["label"] == "good"


def test_normalize_questions_caps_label_length() -> None:
    long = "x" * 500
    out = pt._normalize_questions([long])
    assert len(out[0]["label"]) == 200


# ── Button normalizer ──────────────────────────────────────────────────


def test_normalize_buttons_accepts_valid_actions() -> None:
    out = pt._normalize_buttons([
        {"label": "Working", "action": "progress:working"},
        {"label": "Stuck", "action": "progress:stuck"},
    ])
    assert len(out) == 2


def test_normalize_buttons_drops_unknown_actions() -> None:
    out = pt._normalize_buttons([
        {"label": "OK", "action": "progress:bogus"},
        {"label": "Done", "action": "progress:done"},
    ])
    assert len(out) == 1
    assert out[0]["action"] == "progress:done"


def test_normalize_buttons_drops_empty_labels() -> None:
    out = pt._normalize_buttons([
        {"label": "", "action": "progress:working"},
        {"label": "OK", "action": "progress:done"},
    ])
    assert len(out) == 1


# ── Cron validation ────────────────────────────────────────────────────


@pytest.mark.parametrize("good", [
    "* * * * *",
    "*/30 * * * *",
    "0 * * * *",
    "0 9 * * 1-5",
])
def test_validate_cron_accepts_5_field(good: str) -> None:
    assert pt._validate_cron(good) == good


@pytest.mark.parametrize("bad", [
    "* * * *",        # 4 fields
    "* * * * * *",    # 6 fields
    "",
    "    ",
])
def test_validate_cron_rejects_wrong_field_count(bad: str) -> None:
    with pytest.raises(ValueError):
        pt._validate_cron(bad)


def test_validate_cron_collapses_whitespace() -> None:
    """Multiple spaces between fields → single space; preserved on output."""
    assert pt._validate_cron("0  *   *  *  *") == "0 * * * *"


# ── Default seed shape ─────────────────────────────────────────────────


def test_default_buttons_have_valid_actions() -> None:
    """Sanity: the bundled defaults must pass the button normalizer."""
    out = pt._normalize_buttons(pt._DEFAULT_BUTTONS)
    assert len(out) == len(pt._DEFAULT_BUTTONS)


def test_default_questions_normalize_cleanly() -> None:
    for qs in (pt._GENERIC_QUESTIONS, pt._CODING_QUESTIONS, pt._WRITING_QUESTIONS):
        out = pt._normalize_questions(qs)
        assert len(out) == len(qs)
