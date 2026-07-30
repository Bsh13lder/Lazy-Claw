"""Server-side notification-noise pass (2026-07-30).

One daily 08:00 task with all defaults used to generate ~14-16 visible pings
across three uncoordinated surfaces. The server-side levers tested here:

* Quiet DEFAULTS — offsets ``["0m","-30m"]`` (was 3 entries), nag ladder
  ``[0,15,30]`` (was 5 steps). Both remain user settings.
* Anchor unification — server pre-reminders measure offsets from the TIMED
  DUE when present, exactly like the phone's local alarms
  (``reminderBaseTime`` precedence), so the two surfaces ping at the same
  instants instead of interleaving.
* A pre-reminder can never land ON the reminder instant (that minute belongs
  to the nag ladder's entry 0 — same-minute double-ping).
* Per-caller ``dedup_window`` — the daemon passes 12h for task pushes so a
  whole occurrence ladder collapses into ONE feed row (the default 30-min
  window was shorter than the ladder's own gaps).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications.feed_store import (
    get_notifications_since,
    record_notification,
)
from lazyclaw.settings.general import DEFAULT_GENERAL
from lazyclaw.tasks.pre_reminders import _FALLBACK_OFFSETS, resolve_pre_reminders


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


def test_quiet_defaults():
    assert DEFAULT_GENERAL["reminder_offsets"] == ["0m", "-30m"]
    assert DEFAULT_GENERAL["nag_intervals"] == [0, 15, 30]
    assert _FALLBACK_OFFSETS == ("-30m",)


@pytest.mark.asyncio
async def test_offsets_anchor_on_timed_due(cfg, monkeypatch):
    """Timed due 20:00 Madrid + reminder 19:30 + offset -2h → ping 18:00 Madrid.

    Anchored on reminder_at (the old behaviour) the ping would land 17:30.
    The phone's local alarms anchor on the timed due; the server must too.
    """
    monkeypatch.setenv("LAZYCLAW_DEFAULT_TZ", "Europe/Madrid")
    out = await resolve_pre_reminders(
        cfg, "u1",
        reminder_at="2199-07-31T17:30:00+00:00",  # 19:30 Madrid
        due_date="2199-07-31T20:00:00",  # naive timed due = 20:00 Madrid
        explicit=["-2h"],
    )
    assert [datetime.fromisoformat(t) for t in out] == [
        datetime(2199, 7, 31, 16, 0, tzinfo=timezone.utc)  # 18:00 Madrid
    ]


@pytest.mark.asyncio
async def test_pre_reminder_never_lands_on_reminder_instant(cfg):
    """due 20:00Z with reminder 19:30Z: offset -30m == the reminder minute.

    That minute belongs to the nag ladder's entry 0 — emitting a pre-reminder
    there double-pings the same instant.
    """
    out = await resolve_pre_reminders(
        cfg, "u1",
        reminder_at="2199-07-31T19:30:00+00:00",
        due_date="2199-07-31T20:00:00+00:00",
        explicit=["-30m"],
    )
    assert out == []


@pytest.mark.asyncio
async def test_date_only_due_still_anchors_on_reminder(cfg):
    out = await resolve_pre_reminders(
        cfg, "u1",
        reminder_at="2199-07-31T10:00:00+00:00",
        due_date="2199-07-31",  # date-only → no timed instant
        explicit=["-1h"],
    )
    assert [datetime.fromisoformat(t) for t in out] == [
        datetime(2199, 7, 31, 9, 0, tzinfo=timezone.utc)
    ]


@pytest.mark.asyncio
async def test_dedup_window_override_collapses_ladder_gap(cfg):
    """Two pushes 2h apart with the same task key: default window keeps two
    rows; the daemon's 12h override folds them into one with repeat_count 2.
    """
    first = await record_notification(
        cfg, "u1", "heartbeat", "Reminder", "Medicine — due now",
        dedup_key="task:t1", dedup_window=timedelta(hours=12),
    )
    # Age the row 2h so it falls OUTSIDE the default 30-min window.
    aged = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE notifications SET created_at = ? WHERE id = ?",
            (aged, first["id"]),
        )
        await db.commit()

    await record_notification(
        cfg, "u1", "heartbeat", "Reminder", "Medicine — reminder #2",
        dedup_key="task:t1", dedup_window=timedelta(hours=12),
    )
    feed = await get_notifications_since(cfg, "u1", None)
    rows = [r for r in feed["notifications"] if r.get("dedup_key") == "task:t1"] \
        if feed["notifications"] and "dedup_key" in feed["notifications"][0] \
        else feed["notifications"]
    assert len(rows) == 1
    assert rows[0]["repeat_count"] == 2
