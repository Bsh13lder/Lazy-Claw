"""Regression tests for two task-store bugs.

T1 — ``update_task`` used to encrypt a list-shaped ``tags`` value directly.
``tags`` is in ``ENCRYPTED_FIELDS`` and arrives as a Python ``list`` (from
``UpdateTaskBody.tags``), so ``encrypt(value, key)`` hit ``list.encode`` →
``AttributeError`` → HTTP 500, and the tag edit was silently lost.
``create_task`` did it correctly (``json.dumps(tags)`` first); ``update_task``
did not.

T3 — ``complete_task`` had no status guard, so a second ``/complete`` on an
already-done RECURRING task re-ran the recurring-respawn block and called
``create_task`` again, spawning a DUPLICATE next occurrence. Mobile sync is
at-least-once (a dropped response re-pushes the complete op) and a web
double-click both trigger the second call.

Both tests are store-level and run against an isolated temp DB (never the
live ``./data``) — same DB-isolation pattern as the sibling suite.
"""
from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# T1 — editing tags must round-trip (not 500 and drop the tags)
# ---------------------------------------------------------------------------

async def test_update_task_tags_round_trips(cfg) -> None:
    """Updating ``tags`` with a Python list must persist and read back."""
    task = await task_store.create_task(cfg, "u1", "tag me")

    ok = await task_store.update_task(cfg, "u1", task["id"], tags=["a", "b"])
    assert ok is True

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None
    # The store persists tags as an encrypted JSON string; decrypt+parse it.
    assert json.loads(fetched["tags"]) == ["a", "b"]


async def test_update_task_tags_replaces_existing(cfg) -> None:
    """A tags edit fully replaces the prior list (no merge, no loss)."""
    task = await task_store.create_task(cfg, "u1", "retag", tags=["old"])

    ok = await task_store.update_task(cfg, "u1", task["id"], tags=["new1", "new2"])
    assert ok is True

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None
    assert json.loads(fetched["tags"]) == ["new1", "new2"]


# ---------------------------------------------------------------------------
# T3 — completing a recurring task twice must be idempotent (no duplicate)
# ---------------------------------------------------------------------------

async def test_complete_recurring_task_is_idempotent(cfg) -> None:
    """A second ``complete_task`` on an already-done recurring task must NOT
    spawn a second next occurrence."""
    task = await task_store.create_task(
        cfg, "u1", "daily standup", recurring="0 9 * * *"
    )

    first = await task_store.complete_task(cfg, "u1", task["id"])
    assert first is True
    after_first = await task_store.list_tasks(cfg, "u1")
    # Original (now done) + exactly one freshly-spawned next occurrence.
    assert len(after_first) == 2

    # At-least-once retry / double-click: complete the same (already-done) task.
    second = await task_store.complete_task(cfg, "u1", task["id"])
    assert second is True  # return contract unchanged: still reports success
    after_second = await task_store.list_tasks(cfg, "u1")
    assert len(after_second) == 2, (
        "second complete on an already-done recurring task duplicated the "
        "next occurrence"
    )


async def test_complete_recurring_preserves_reminder_time_of_day(cfg) -> None:
    """The respawned occurrence must keep the reminder at its ORIGINAL local
    wall-clock time-of-day.

    Regression: an 08:00 daily medication reminder drifted (the reporter saw
    ~09:30) on every completion. ``complete_task`` rebuilt the next reminder
    as ``get_next_run(cron) + reminder_offset_minutes``, but that offset was
    derived against a date-only (midnight) ``due_date`` so it encoded the
    whole time-of-day; adding it onto a cron fire that already carried 08:00
    double-counted the hour. The fix carries the original H:M:S onto the new
    date instead.
    """
    from datetime import datetime

    from lazyclaw.lazybrain.timezone_util import user_tz

    tz = user_tz(None)
    # A concrete 08:00 *local* instant (matches the ``0 8 * * *`` cron hour).
    eight_local = datetime.now(tz).replace(hour=8, minute=0, second=0, microsecond=0)

    task = await task_store.create_task(
        cfg, "u1", "take meds",
        recurring="0 8 * * *",
        reminder_at=eight_local.isoformat(),
    )

    ok = await task_store.complete_task(cfg, "u1", task["id"])
    assert ok is True

    spawned = [t for t in await task_store.list_tasks(cfg, "u1") if t["status"] != "done"]
    assert len(spawned) == 1, "exactly one next occurrence should be spawned"

    next_rem = spawned[0]["reminder_at"]
    assert next_rem is not None, "respawned occurrence lost its reminder"
    next_local = datetime.fromisoformat(next_rem).astimezone(tz)
    assert (next_local.hour, next_local.minute) == (8, 0), (
        f"reminder drifted off 08:00 local: got {next_local.isoformat()}"
    )


async def test_complete_recurring_preserves_naive_local_reminder(cfg) -> None:
    """A NAIVE (no-timezone) reminder — exactly what the mobile app persists
    (``reschedule_dates.dart`` writes ``YYYY-MM-DDTHH:MM:SS`` with no offset) —
    must respawn at the SAME wall-clock the user sees, not shifted by the UTC
    offset.

    Mobile both stores and renders a naive reminder as *local* time (Dart
    ``DateTime.tryParse`` → local). The respawn used to force naive → UTC and
    re-emit a ``+00:00`` string, so an 08:00 reminder came back as 08:00 UTC =
    10:00 Madrid on the phone. The respawn must be format- and wall-clock-
    preserving: a naive original stays naive, only the date rolls forward.
    """
    from datetime import datetime

    from lazyclaw.lazybrain.timezone_util import user_tz

    tz = user_tz(None)
    # Mobile-shaped payload: naive local 08:00 (NO tz suffix, NO 'Z').
    naive_eight = (
        datetime.now(tz)
        .replace(hour=8, minute=0, second=0, microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%S")
    )
    assert "+" not in naive_eight and "Z" not in naive_eight  # truly naive

    task = await task_store.create_task(
        cfg, "u1", "take meds",
        recurring="0 8 * * *",
        reminder_at=naive_eight,
    )

    ok = await task_store.complete_task(cfg, "u1", task["id"])
    assert ok is True

    spawned = [
        t for t in await task_store.list_tasks(cfg, "u1") if t["status"] != "done"
    ]
    assert len(spawned) == 1, "exactly one next occurrence should be spawned"

    next_rem = spawned[0]["reminder_at"]
    assert next_rem is not None, "respawned occurrence lost its reminder"
    # Interpret exactly as the mobile app does: naive → local wall-clock as-is;
    # tz-aware → convert to local. Either way the user must still see 08:00.
    parsed = datetime.fromisoformat(next_rem)
    shown_local = parsed if parsed.tzinfo is None else parsed.astimezone(tz)
    assert (shown_local.hour, shown_local.minute) == (8, 0), (
        f"naive reminder drifted off 08:00 local: stored={next_rem!r} "
        f"shown={shown_local.isoformat()}"
    )


async def test_create_task_normalizes_naive_reminder_to_utc(cfg) -> None:
    """A naive (mobile-shaped) ``reminder_at`` must be stored as a UTC-aware
    instant representing the user's LOCAL wall-clock.

    The heartbeat fires reminders by comparing ``reminder_at`` against
    ``datetime.now(timezone.utc)`` (a lexical string compare in SQL). A naive
    ``08:00`` sorts as if it were 08:00 UTC, so a Madrid user's reminder fired
    ~2h late. Normalising naive → user-local → UTC at the write boundary makes
    every consumer (firing, respawn, display) read the one canonical format the
    rest of the backend already uses (``nl_time`` etc. all emit UTC-aware).
    """
    from datetime import datetime

    from lazyclaw.lazybrain.timezone_util import user_tz

    tz = user_tz(None)
    naive_eight = (
        datetime.now(tz)
        .replace(hour=8, minute=0, second=0, microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%S")
    )
    assert "+" not in naive_eight and "Z" not in naive_eight  # truly naive

    task = await task_store.create_task(cfg, "u1", "meds", reminder_at=naive_eight)
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None

    stored = fetched["reminder_at"]
    parsed = datetime.fromisoformat(stored)
    assert parsed.tzinfo is not None, (
        f"naive reminder_at not normalised to UTC-aware: {stored!r}"
    )
    assert parsed.astimezone(tz).hour == 8, (
        f"normalised reminder no longer represents 08:00 local: {stored!r}"
    )


async def test_update_task_normalizes_naive_reminder_to_utc(cfg) -> None:
    """The same normalisation applies to edits, not just creates."""
    from datetime import datetime

    from lazyclaw.lazybrain.timezone_util import user_tz

    tz = user_tz(None)
    task = await task_store.create_task(cfg, "u1", "meds")

    naive_nine = (
        datetime.now(tz)
        .replace(hour=9, minute=0, second=0, microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%S")
    )
    ok = await task_store.update_task(cfg, "u1", task["id"], reminder_at=naive_nine)
    assert ok is True

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None
    parsed = datetime.fromisoformat(fetched["reminder_at"])
    assert parsed.tzinfo is not None
    assert parsed.astimezone(tz).hour == 9


async def test_create_task_leaves_aware_reminder_unchanged(cfg) -> None:
    """An already-UTC-aware reminder (agent / nl_time path) passes through
    untouched — normalisation must be idempotent, not double-shift."""
    from datetime import datetime, timezone

    aware = datetime(2026, 7, 26, 6, 0, 0, tzinfo=timezone.utc).isoformat()
    task = await task_store.create_task(cfg, "u1", "meds", reminder_at=aware)
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None
    # Same instant, still UTC.
    parsed = datetime.fromisoformat(fetched["reminder_at"])
    assert parsed == datetime(2026, 7, 26, 6, 0, 0, tzinfo=timezone.utc)


async def test_complete_recurring_legacy_naive_row_respawns_on_time(cfg) -> None:
    """Defense-in-depth: a LEGACY row whose ``reminder_at`` is still naive
    (written before write-time normalisation existed) must still respawn at the
    right local wall-clock. Simulated by forcing a naive value directly into
    the row, bypassing ``create_task``'s normalisation.
    """
    from datetime import datetime

    from lazyclaw.db.connection import db_session
    from lazyclaw.lazybrain.timezone_util import user_tz

    tz = user_tz(None)
    task = await task_store.create_task(cfg, "u1", "legacy meds", recurring="0 8 * * *")

    naive_eight = (
        datetime.now(tz)
        .replace(hour=8, minute=0, second=0, microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%S")
    )
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE tasks SET reminder_at = ? WHERE id = ?",
            (naive_eight, task["id"]),
        )
        await db.commit()

    ok = await task_store.complete_task(cfg, "u1", task["id"])
    assert ok is True

    spawned = [
        t for t in await task_store.list_tasks(cfg, "u1") if t["status"] != "done"
    ]
    assert len(spawned) == 1
    parsed = datetime.fromisoformat(spawned[0]["reminder_at"])
    shown_local = parsed if parsed.tzinfo is None else parsed.astimezone(tz)
    assert (shown_local.hour, shown_local.minute) == (8, 0), (
        f"legacy naive reminder drifted: stored={spawned[0]['reminder_at']!r}"
    )


async def test_complete_non_recurring_task_still_works(cfg) -> None:
    """The idempotency guard must not change non-recurring completion."""
    task = await task_store.create_task(cfg, "u1", "one-off")

    ok = await task_store.complete_task(cfg, "u1", task["id"])
    assert ok is True

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched is not None
    assert fetched["status"] == "done"
    assert fetched["completed_at"] is not None
    # No respawn for a non-recurring task.
    assert len(await task_store.list_tasks(cfg, "u1")) == 1
