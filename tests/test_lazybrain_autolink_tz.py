"""Regression: save_note's day-based auto-link must use the USER's timezone
day, not UTC.

Bug (2026-05-29): ``store.save_note`` derived the journal-link day from
``_now()`` (UTC) while journal pages are titled via
``timezone_util.today_iso`` (Europe/Madrid). A note saved between local
midnight and the UTC offset (e.g. 00:30 Madrid = 22:30 UTC the day before)
got an ``_[[Journal — <yesterday>]]_`` wikilink, so clicking it opened the
wrong day's journal — or an unresolved stub.

These tests pin the fix: when the user-TZ day and the UTC day disagree, the
auto-link must point at the user-TZ journal page.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store, timezone_util


@pytest.fixture
async def tz_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-tz", "tzuser", "x", "salt-tz-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_autolink_uses_user_tz_day_not_utc(tz_config, monkeypatch) -> None:
    """It is 00:30 in Madrid on 2026-05-29 (= 22:30 UTC on 2026-05-28).
    The new note must link to *today's* (Madrid) journal page."""
    # User-TZ day = 2026-05-29; UTC wall clock = the previous calendar day.
    monkeypatch.setattr(timezone_util, "today_iso", lambda user_id=None: "2026-05-29")
    monkeypatch.setattr(store, "_now", lambda: "2026-05-28 22:30:00")

    # Seed BOTH journal pages so the assertion is unambiguous: the old
    # (buggy) UTC path would resolve 2026-05-28; the fixed path resolves
    # 2026-05-29. With both present, only a wrong day-derivation could pick
    # the UTC one.
    await store.save_note(tz_config, "u-tz", content="y", title="Journal — 2026-05-28")
    await store.save_note(tz_config, "u-tz", content="t", title="Journal — 2026-05-29")

    note = await store.save_note(tz_config, "u-tz", content="bought coffee 2 EUR")
    saved = await store.get_note(tz_config, "u-tz", note["id"])
    assert saved is not None

    assert "[[Journal — 2026-05-29]]" in saved["content"], (
        "auto-link must target the user-TZ (Madrid) journal day"
    )
    assert "[[Journal — 2026-05-28]]" not in saved["content"], (
        "auto-link must NOT regress to the UTC day"
    )


@pytest.mark.asyncio
async def test_autolink_matches_journal_for_same_tz_day(tz_config, monkeypatch) -> None:
    """Sanity: when UTC and user-TZ agree, the link still resolves."""
    monkeypatch.setattr(timezone_util, "today_iso", lambda user_id=None: "2026-05-29")
    monkeypatch.setattr(store, "_now", lambda: "2026-05-29 10:00:00")

    await store.save_note(tz_config, "u-tz", content="t", title="Journal — 2026-05-29")
    note = await store.save_note(tz_config, "u-tz", content="paid rent")
    saved = await store.get_note(tz_config, "u-tz", note["id"])
    assert saved is not None
    assert "[[Journal — 2026-05-29]]" in saved["content"]
