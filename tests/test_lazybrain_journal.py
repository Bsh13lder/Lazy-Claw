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


def test_sanitize_wikilink_strips_reserved_chars() -> None:
    assert journal._sanitize_wikilink("Hello [World] | foo#bar\nbaz") == (
        "Hello World foo bar baz"
    )


def test_sanitize_wikilink_preserves_clean_titles() -> None:
    assert journal._sanitize_wikilink("Decision: switch DB") == "Decision: switch DB"


def test_sanitize_wikilink_caps_at_100_chars() -> None:
    long = "x" * 200
    assert len(journal._sanitize_wikilink(long)) == 100


def test_sanitize_wikilink_empty_when_only_reserved() -> None:
    assert journal._sanitize_wikilink("[]|#\n") == ""


@pytest.mark.asyncio
async def test_link_note_today_no_op_on_empty_title(monkeypatch) -> None:
    """Empty/all-reserved titles must skip the journal write entirely."""
    called = []

    async def fake_append(*args, **kwargs):
        called.append((args, kwargs))
        return {}

    monkeypatch.setattr(journal, "append_journal", fake_append)
    await journal.link_note_today(None, "user-1", title="[]|#", kind="decision")
    assert called == []


@pytest.mark.asyncio
async def test_link_note_today_emits_wikilink_bullet(monkeypatch) -> None:
    """Helper must format `[[Title]] — kind` so the wikilink reindexer
    creates a graph edge from today's journal page to the target note."""
    captured: dict = {}

    async def fake_append(config, user_id, content, iso_date=None):
        captured["content"] = content
        captured["user_id"] = user_id
        return {"id": "note-1"}

    monkeypatch.setattr(journal, "append_journal", fake_append)
    await journal.link_note_today(
        None, "user-1", title="Decision: switch DB", kind="decision",
    )
    assert "[[Decision: switch DB]] — decision" == captured["content"]
    assert captured["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_link_note_today_swallows_exceptions(monkeypatch) -> None:
    """A PKM bookkeeping failure must never propagate to the caller."""

    async def boom(*_a, **_k):
        raise RuntimeError("simulated db failure")

    monkeypatch.setattr(journal, "append_journal", boom)
    # No exception should escape.
    await journal.link_note_today(None, "user-1", title="X", kind="til")
