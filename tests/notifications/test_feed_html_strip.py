"""The Notification Center must never show raw markup.

Reported 2026-07-30: phone notifications displayed literal tags —
"⏰ </b> Medicine" style. Producers build Telegram-HTML strings and the
heartbeat funnel recorded them verbatim into the feed; the mobile app
renders title/body as plain text. ``record_notification`` is the single
choke point every feed writer passes through, so the strip lives there.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications.feed_store import (
    get_notifications_since,
    record_notification,
    strip_html,
    strip_markdown,
)


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "testuser", "x", "salt-test"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


def test_strip_html_flattens_tags_and_entities():
    assert strip_html("⏰ <b>Medicine</b>\n<i>in 30m</i>") == "⏰ Medicine\nin 30m"
    assert strip_html("a &amp; b") == "a & b"
    assert strip_html("") == ""
    assert strip_html(None) == ""


def test_strip_markdown_flattens_common_markup():
    assert strip_markdown("**bold** and `code`") == "bold and code"
    assert strip_markdown("[link](http://x)") == "link (http://x)"


@pytest.mark.asyncio
async def test_record_notification_strips_html(cfg):
    await record_notification(
        cfg, "u1", "heartbeat",
        "⏰ <b>Medicine</b>",
        "⏰ <b>Medicine</b>\n<i>in 30 minutes</i>",
    )
    feed = await get_notifications_since(cfg, "u1", None)
    rows = feed["notifications"]
    assert len(rows) == 1
    assert "<b>" not in rows[0]["title"]
    assert "</b>" not in rows[0]["body"]
    assert "<i>" not in rows[0]["body"]
    assert rows[0]["title"] == "⏰ Medicine"
    assert rows[0]["body"] == "⏰ Medicine\nin 30 minutes"
