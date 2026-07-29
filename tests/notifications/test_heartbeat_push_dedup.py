"""Heartbeat pushes must collapse identical repeats instead of spamming the feed.

Reported 2026-07-27: "notification log is noisy". Measured against the live
feed: 418 rows, and ``dedup_key`` is NULL on **every single one** — the spine
ships a working dedup path (``feed_store.record_notification`` collapses a
recent unread row with a matching key and bumps ``repeat_count``) that no
caller has ever used. Same day: 17 ``heartbeat`` rows, six of them exact pairs
written 3-6 ms apart, so the same push was recorded twice with nothing able to
merge them.

``deliver_heartbeat_push`` is the single writer of ``kind="heartbeat"``. Keying
on the message CONTENT is the right granularity for it: it receives only the
rendered text, and two pushes carrying identical text within the dedup window
ARE the same event — while two different tasks firing in one tick render
different text and must stay separate rows.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications import heartbeat_push as hb

pytestmark = pytest.mark.asyncio


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


@pytest.fixture(autouse=True)
def _route_to_u1(monkeypatch):
    """Pin admin resolution + channel so the durable-record leg always runs."""
    async def _admin(_config):
        return "u1"

    async def _channel(_config, _user_id):
        return "app"          # app-only: records to the feed, skips Telegram

    monkeypatch.setattr(hb, "resolve_admin_user_id", _admin)
    monkeypatch.setattr(hb, "get_notification_channel", _channel)


async def _rows(cfg: Config) -> list[tuple]:
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT id, kind, dedup_key FROM notifications ORDER BY created_at"
        )
        return list(await cur.fetchall())


async def _noop() -> None:
    return None


async def test_identical_pushes_collapse_into_one_row(cfg) -> None:
    """The exact duplicate-pair case seen in production."""
    text = "⏰ <b>Take medication</b>\n<i>in 2 hours</i>"

    await hb.deliver_heartbeat_push(cfg, text, telegram_send=_noop)
    await hb.deliver_heartbeat_push(cfg, text, telegram_send=_noop)

    rows = await _rows(cfg)
    assert len(rows) == 1, (
        f"the same heartbeat push was recorded {len(rows)}× — repeats must "
        "collapse into one row, not spam the Notification Center"
    )


async def test_heartbeat_rows_carry_a_dedup_key(cfg) -> None:
    """Without a key the collapse can never happen — 418/418 live rows had NULL."""
    await hb.deliver_heartbeat_push(cfg, "watcher hit on upwork.com", telegram_send=_noop)

    rows = await _rows(cfg)
    assert len(rows) == 1
    assert rows[0][2], "heartbeat notification was recorded with a NULL dedup_key"


async def test_different_pushes_stay_separate(cfg) -> None:
    """Two different tasks firing in the same tick are NOT duplicates.

    Guards the fix against over-collapsing: a content-derived key must keep
    distinct messages distinct.
    """
    await hb.deliver_heartbeat_push(cfg, "⏰ Take medication", telegram_send=_noop)
    await hb.deliver_heartbeat_push(cfg, "⏰ Call the dentist", telegram_send=_noop)

    rows = await _rows(cfg)
    assert len(rows) == 2, "distinct heartbeat messages were wrongly merged"
    assert rows[0][2] != rows[1][2], "distinct messages must derive distinct keys"


async def test_caller_supplied_key_collapses_a_whole_task_occurrence(cfg) -> None:
    """One task must produce ONE feed row, not one per nag step.

    Measured on the live feed for a single "Medicine" task in one morning:

        05:00  ⏰ Medicine · in 1h            ← pre-reminder
        06:02  ⏰ Medicine · in 30m           ← pre-reminder
        06:02  ⏰ Reminder · Medicine 08:02   ← nag #1
        06:18  ⏰ Reminder · Medicine 08:18 · reminder #2
        06:54  ⏰ Reminder · Medicine 08:54 · reminder #3

    Five rows for one dose — times three daily medicine tasks. The content hash
    cannot merge them because every line legitimately differs (each carries its
    own lead time / nag counter), so the key has to come from the CALLER, which
    knows they are all the same task occurrence.
    """
    texts = [
        "⏰ <b>Medicine</b>\n<i>in 1h</i>",
        "⏰ <b>Medicine</b>\n<i>in 30m</i>",
        "⏰ Reminder · Medicine\nMedicine · ⏰ 08:02",
        "⏰ Reminder · Medicine\nMedicine · ⏰ 08:18 · reminder #2",
        "⏰ Reminder · Medicine\nMedicine · ⏰ 08:54 · reminder #3",
    ]
    for text in texts:
        await hb.deliver_heartbeat_push(
            cfg, text, telegram_send=_noop, dedup_key="task:med-1",
        )

    rows = await _rows(cfg)
    assert len(rows) == 1, (
        f"one task occurrence produced {len(rows)} feed rows — the escalation "
        "ladder spams the Notification Center"
    )


async def test_caller_key_keeps_different_tasks_apart(cfg) -> None:
    """Collapsing is per-task, never across tasks."""
    await hb.deliver_heartbeat_push(
        cfg, "⏰ Medicine in 1h", telegram_send=_noop, dedup_key="task:med-1",
    )
    await hb.deliver_heartbeat_push(
        cfg, "⏰ Antibiotics in 1h", telegram_send=_noop, dedup_key="task:abx-2",
    )

    assert len(await _rows(cfg)) == 2


async def test_latest_message_wins_on_collapse(cfg) -> None:
    """The surviving row must show the MOST RECENT state, not the first.

    A row frozen on "in 1h" while the task is already overdue would be worse
    than the spam it replaces.
    """
    from lazyclaw.notifications.feed_store import get_notifications_since

    await hb.deliver_heartbeat_push(
        cfg, "⏰ Medicine · in 1h", telegram_send=_noop, dedup_key="task:med-1",
    )
    await hb.deliver_heartbeat_push(
        cfg, "⏰ Medicine · reminder #3", telegram_send=_noop, dedup_key="task:med-1",
    )

    items = (await get_notifications_since(cfg, "u1"))["notifications"]
    assert len(items) == 1
    assert "reminder #3" in items[0]["body"]
    assert (items[0].get("meta") or {}).get("repeat_count") == 2


async def test_collapsed_row_records_how_many_times_it_repeated(cfg) -> None:
    """Collapsing must not hide the repeat — the row carries ``repeat_count``
    so the UI can render "×3" instead of silently dropping two events."""
    from lazyclaw.notifications.feed_store import get_notifications_since

    text = "⏰ <b>Weekly review</b>"
    for _ in range(3):
        await hb.deliver_heartbeat_push(cfg, text, telegram_send=_noop)

    feed = await get_notifications_since(cfg, "u1")
    items = feed["notifications"]
    assert len(items) == 1
    assert (items[0].get("meta") or {}).get("repeat_count") == 3
