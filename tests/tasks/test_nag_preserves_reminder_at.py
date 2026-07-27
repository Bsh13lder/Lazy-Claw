"""``reminder_at`` is user data — the nag ladder must not overwrite it.

Reported symptom (2026-07-26): "first problem is notifications" on recurring
tasks. Two independent audit lenses traced it here.

``_check_task_nagging`` escalated by pushing ``reminder_at`` forward to *now* on
every nag claim, reusing the user's canonical "fire at 08:00" value as mutable
escalation state. Two things break:

  1. The task's displayed/authoritative reminder time walks forward every nag
     (08:00 → 08:15 → 08:45 → 10:30 …).
  2. ``complete_task``'s recurring respawn reads ``task["reminder_at"]`` to carry
     the original wall-clock time-of-day onto the next occurrence. It therefore
     inherits *the last nag's clock*, so a daily 08:00 reminder drifts later on
     every cycle — compounding, unbounded, and invisible.

The escalation cursor is ``nag_fired_at``, which the same atomic claim already
stamps. It exists precisely for this and is reset to NULL whenever the user
edits ``reminder_at`` (``store.update_task``), so "no nag yet" correctly falls
back to the reminder itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.heartbeat.daemon import HeartbeatDaemon
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
    """A daemon whose only wired dependency is a no-op Telegram push.

    ``_check_task_nagging`` early-returns without a push target, so the fake is
    required to exercise the claim path at all.
    """
    async def _push(*args, **kwargs):
        return None

    return HeartbeatDaemon(cfg, lane_queue=None, telegram_push=_push)


async def _read(cfg: Config, task_id: str) -> tuple[str, int, str | None]:
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT reminder_at, nag_count, nag_fired_at FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = await cur.fetchone()
    return row[0], row[1], row[2]


async def _make_overdue(cfg: Config, minutes_ago: int = 5) -> tuple[str, str]:
    """A live task whose reminder fired ``minutes_ago`` and has never nagged."""
    task = await task_store.create_task(cfg, "u1", "take meds")
    due = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE tasks SET reminder_at = ?, status = 'todo', "
            "nag_count = 0, nag_fired_at = NULL WHERE id = ?",
            (due, task["id"]),
        )
        await db.commit()
    return task["id"], due


async def test_first_nag_does_not_move_reminder_at(cfg) -> None:
    """Firing a nag must stamp ``nag_fired_at`` and leave ``reminder_at`` alone."""
    task_id, original = await _make_overdue(cfg)

    await _daemon(cfg)._check_task_nagging()

    reminder_at, nag_count, nag_fired_at = await _read(cfg, task_id)
    assert nag_count == 1, "the nag should have been claimed"
    assert nag_fired_at is not None, "the claim must stamp the escalation cursor"
    assert reminder_at == original, (
        "the nag ladder overwrote the user's reminder time — a recurring task's "
        f"respawn then inherits the nag clock and drifts (was {original!r}, "
        f"now {reminder_at!r})"
    )


async def test_repeated_nags_never_drift_reminder_at(cfg) -> None:
    """Across a full escalation ladder ``reminder_at`` stays put.

    Each pass is forced due by ageing ``nag_fired_at`` past the escalation
    interval, which is exactly what the daemon compares against.
    """
    task_id, original = await _make_overdue(cfg)
    daemon = _daemon(cfg)

    for _ in range(4):
        await daemon._check_task_nagging()
        # Age the claim so the next escalation step is due on the next pass.
        aged = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        async with db_session(cfg) as db:
            await db.execute(
                "UPDATE tasks SET nag_fired_at = ? WHERE id = ?", (aged, task_id),
            )
            await db.commit()

    reminder_at, nag_count, _ = await _read(cfg, task_id)
    assert nag_count > 1, f"the ladder should have escalated (nag_count={nag_count})"
    assert reminder_at == original, (
        f"reminder_at drifted across {nag_count} nags: {original!r} → {reminder_at!r}"
    )


async def test_recurring_respawn_after_nagging_keeps_original_time(cfg) -> None:
    """End-to-end: the reported symptom.

    A daily 08:00 task the user ignores through several nags, then completes,
    must respawn at 08:00 — not at whatever time the last nag fired.
    """
    from lazyclaw.lazybrain.timezone_util import user_tz

    tz = user_tz(None)
    eight_local = datetime.now(tz).replace(
        hour=8, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)

    task = await task_store.create_task(
        cfg, "u1", "take meds",
        recurring="0 8 * * *",
        reminder_at=eight_local.isoformat(),
    )
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE tasks SET status = 'todo' WHERE id = ?", (task["id"],),
        )
        await db.commit()

    daemon = _daemon(cfg)
    for _ in range(3):
        await daemon._check_task_nagging()
        aged = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        async with db_session(cfg) as db:
            await db.execute(
                "UPDATE tasks SET nag_fired_at = ? WHERE id = ?",
                (aged, task["id"]),
            )
            await db.commit()

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = [
        t for t in await task_store.list_tasks(cfg, "u1") if t["status"] != "done"
    ]
    assert len(spawned) == 1
    next_rem = spawned[0]["reminder_at"]
    assert next_rem is not None
    parsed = datetime.fromisoformat(next_rem)
    shown = parsed if parsed.tzinfo is None else parsed.astimezone(tz)
    assert (shown.hour, shown.minute) == (8, 0), (
        "the respawned occurrence inherited the nag clock instead of the user's "
        f"08:00 (got {shown.isoformat()})"
    )
