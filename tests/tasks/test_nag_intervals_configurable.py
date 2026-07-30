"""The task-nag escalation ladder must be user-configurable.

Reported 2026-07-29: notifications too noisy. One medicine dose fired up to
five server nags (at 0 / +15 / +30 / +60 / +60 minutes) on top of its advance
reminders, and the ladder was a hard-coded literal:

    intervals = [0, 15, 30, 60, 60]      # daemon._check_task_nagging
    ... AND nag_count < 5                # the matching SQL cap

Both numbers now come from ``users.settings.general.nag_intervals``, so the
list defines the cadence AND the count together — they can no longer disagree.

Semantics:
  * entry 0 is the AT-TIME reminder (offset 0 from the reminder instant);
    every later entry is minutes since the previous nag fired
  * ``[0]``  → one reminder, no escalation (the quiet setting)
  * ``[]``   → normalised to ``[0]``; a task reminder that never fires at all
    is a footgun, not a preference
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.heartbeat.daemon import HeartbeatDaemon
from lazyclaw.settings.general import (
    DEFAULT_GENERAL,
    get_general_settings,
    update_general_settings,
)
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


def _daemon(cfg: Config) -> HeartbeatDaemon:
    async def _push(*args, **kwargs):
        return None

    return HeartbeatDaemon(cfg, lane_queue=None, telegram_push=_push)


async def _overdue_task(cfg: Config) -> str:
    task = await task_store.create_task(cfg, "u1", "take meds")
    due = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE tasks SET reminder_at = ?, status = 'todo', nag_count = 0, "
            "nag_fired_at = NULL WHERE id = ?",
            (due, task["id"]),
        )
        await db.commit()
    return task["id"]


async def _nag_count(cfg: Config, task_id: str) -> int:
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT nag_count FROM tasks WHERE id = ?", (task_id,)
        )
        return (await cur.fetchone())[0]


async def _run_ladder_to_exhaustion(cfg: Config, task_id: str, rounds: int = 8) -> int:
    """Fire the nag pass repeatedly, ageing the cursor so each step is due."""
    daemon = _daemon(cfg)
    for _ in range(rounds):
        await daemon._check_task_nagging()
        aged = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        async with db_session(cfg) as db:
            await db.execute(
                "UPDATE tasks SET nag_fired_at = ? WHERE id = ?", (aged, task_id),
            )
            await db.commit()
    return await _nag_count(cfg, task_id)


async def test_default_matches_the_quiet_ladder(cfg) -> None:
    """The quiet default (2026-07-30 noise pass): at-time + two escalations."""
    assert DEFAULT_GENERAL["nag_intervals"] == [0, 15, 30]

    gen = await get_general_settings(cfg, "u1")
    assert gen["nag_intervals"] == [0, 15, 30]

    task_id = await _overdue_task(cfg)
    assert await _run_ladder_to_exhaustion(cfg, task_id) == 3


async def test_single_entry_gives_one_reminder_and_no_escalation(cfg) -> None:
    """The quiet setting: remind me once, then leave me alone."""
    await update_general_settings(cfg, "u1", {"nag_intervals": [0]})

    task_id = await _overdue_task(cfg)
    fired = await _run_ladder_to_exhaustion(cfg, task_id)
    assert fired == 1, (
        f"expected exactly one reminder with nag_intervals=[0], got {fired}"
    )


async def test_shorter_ladder_caps_the_count(cfg) -> None:
    """The list length IS the cap — the SQL guard can't drift from the list."""
    await update_general_settings(cfg, "u1", {"nag_intervals": [0, 20]})

    task_id = await _overdue_task(cfg)
    assert await _run_ladder_to_exhaustion(cfg, task_id) == 2


async def test_longer_ladder_is_honoured(cfg) -> None:
    """Someone who wants MORE nagging can have it — the old hard cap of 5 is gone."""
    await update_general_settings(cfg, "u1", {"nag_intervals": [0, 5, 5, 5, 5, 5, 5]})

    task_id = await _overdue_task(cfg)
    assert await _run_ladder_to_exhaustion(cfg, task_id, rounds=10) == 7


async def test_empty_list_normalises_to_one_reminder(cfg) -> None:
    """An empty ladder must still deliver the reminder itself."""
    await update_general_settings(cfg, "u1", {"nag_intervals": []})

    gen = await get_general_settings(cfg, "u1")
    assert gen["nag_intervals"] == [0]


async def test_validation_rejects_bad_input(cfg) -> None:
    for bad in ("nope", [0, -5], [0, "x"], [5, 15]):
        with pytest.raises(ValueError):
            await update_general_settings(cfg, "u1", {"nag_intervals": bad})
