"""Bounded recurring series — ``recur_until`` ("medicines for two weeks").

Behavioral guard on purpose: ``recur_until`` is a ``create_task`` kwarg, so
``test_every_task_column_has_a_respawn_disposition``'s signature introspection
is satisfied by the parameter merely EXISTING — it cannot see whether the
respawn actually passes it (the ``trace_session_id`` precedent). These tests
complete real occurrences and assert on the rows that come out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.tasks import store as task_store

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


async def _open_tasks(cfg):
    return [
        t for t in await task_store.list_tasks(cfg, "u1")
        if t["status"] != "done"
    ]


async def test_respawn_carries_recur_until(cfg, monkeypatch):
    monkeypatch.setenv("LAZYCLAW_DEFAULT_TZ", "Europe/Madrid")
    until = (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat()
    task = await task_store.create_task(
        cfg, "u1", title="medicines",
        due_date=datetime.now(timezone.utc).date().isoformat(),
        reminder_at=datetime.now(timezone.utc).isoformat(),
        recurring="0 9,21 * * *",
        recur_until=until,
    )
    assert task["recur_until"] == until

    assert await task_store.complete_task(cfg, "u1", task["id"])
    respawned = await _open_tasks(cfg)
    assert len(respawned) == 1, "series far from its end must respawn"
    assert respawned[0]["recur_until"] == until, (
        "the next occurrence dropped recur_until — the series would repeat "
        "forever after one completion"
    )


async def test_series_stops_cleanly_at_until(cfg, monkeypatch):
    """Completing the final occurrence ends the series: no next row, no
    respawn-failure alert — a 'series finished' notification instead."""
    monkeypatch.setenv("LAZYCLAW_DEFAULT_TZ", "Europe/Madrid")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    task = await task_store.create_task(
        cfg, "u1", title="antibiotics",
        due_date=yesterday,
        reminder_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        recurring="0 9 * * *",
        recur_until=yesterday,  # the course already ran out
    )

    assert await task_store.complete_task(cfg, "u1", task["id"])

    assert await _open_tasks(cfg) == [], "expired series must not respawn"

    done = await task_store.get_task(cfg, "u1", task["id"])
    assert done["last_error"] is None, (
        "a deliberate series end must not be recorded as a respawn FAILURE"
    )

    from lazyclaw.notifications.feed_store import get_notifications_since

    feed = await get_notifications_since(cfg, "u1", None)
    kinds = {n["kind"] for n in feed["notifications"]}
    assert "task_series_finished" in kinds
    assert "task_respawn_failed" not in kinds


async def test_recur_until_end_of_day_keeps_final_day_occurrences(cfg, monkeypatch):
    """A date-only until means END of that day in the user's tz.

    The respawn always lands on the NEXT cron day carrying the completed
    occurrence's time-of-day, so the discriminating boundary is: until =
    tomorrow, next occurrence tomorrow MORNING. A naive-midnight reading of
    'until' would compare tomorrow-08:00 > tomorrow-00:00 and wrongly end
    the series a full day early — dropping the final day's dose.
    """
    monkeypatch.setenv("LAZYCLAW_DEFAULT_TZ", "Europe/Madrid")
    from lazyclaw.lazybrain.timezone_util import user_tz

    tz = user_tz("u1")
    now_local = datetime.now(tz)
    tomorrow_local = (now_local + timedelta(days=1)).date().isoformat()
    # Completed occurrence anchored this morning 08:00 local → next
    # occurrence tomorrow ~08:00 local, well before tomorrow's 23:59:59.
    this_morning = now_local.replace(hour=8, minute=0, second=0, microsecond=0)
    task = await task_store.create_task(
        cfg, "u1", title="final-day dose",
        due_date=now_local.date().isoformat(),
        reminder_at=this_morning.astimezone(timezone.utc).isoformat(),
        recurring="0 8 * * *",
        recur_until=tomorrow_local,  # series ends TOMORROW at end-of-day
    )
    assert await task_store.complete_task(cfg, "u1", task["id"])
    respawned = await _open_tasks(cfg)
    assert len(respawned) == 1, (
        "the occurrence ON the recur_until day was dropped — date-only "
        "until must mean end-of-day in the user's tz, not midnight"
    )
    assert respawned[0]["recur_until"] == tomorrow_local
    # (Ending the series after that final dose is covered by
    # test_series_stops_cleanly_at_until — completing a future-anchored
    # occurrence EARLY deliberately re-arms the same still-future slot, so
    # the end can't be exercised here without time travel.)


async def test_update_task_sets_and_clears_recur_until(cfg):
    task = await task_store.create_task(
        cfg, "u1", title="stretching", recurring="0 9 * * *",
    )
    until = "2199-08-12"
    assert await task_store.update_task(cfg, "u1", task["id"], recur_until=until)
    assert (await task_store.get_task(cfg, "u1", task["id"]))["recur_until"] == until
    # Empty string clears (mirrors the recurring convention).
    assert await task_store.update_task(cfg, "u1", task["id"], recur_until="")
    assert (await task_store.get_task(cfg, "u1", task["id"]))["recur_until"] is None


async def test_invalid_recur_until_rejected_loudly(cfg):
    with pytest.raises(ValueError):
        await task_store.create_task(
            cfg, "u1", title="bad", recurring="0 9 * * *",
            recur_until="in two weeks",
        )
