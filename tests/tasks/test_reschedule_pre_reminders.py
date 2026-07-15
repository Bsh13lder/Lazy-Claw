"""T4 — rescheduling a task must recompute its advance ``pre_reminders``.

Regression: ``update_task`` kept ``reminder_offset_minutes`` and the reminder
job in sync when ``reminder_at``/``due_date`` changed, but NEVER recomputed the
absolute ``pre_reminders`` timestamps. So a task moved from Mon→Fri kept its
"-2h/-1h before Monday" advance reminders — they fired at the OLD time (or, once
Monday passed, never). The fix lives in the store so EVERY caller benefits (the
``/reschedule`` route, the mobile/web PATCH route, and chat edits) without each
re-deriving the list.

Rule: when ``reminder_at`` or ``due_date`` changes and the caller did NOT pass
``pre_reminders`` explicitly, re-derive them from the user's ``reminder_offsets``
against the NEW ``reminder_at``. Clearing ``reminder_at`` collapses them to NULL.
An explicit ``pre_reminders`` in the same update still wins verbatim.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.settings.general import update_general_settings
from lazyclaw.tasks import store as task_store
from lazyclaw.tasks.pre_reminders import resolve_pre_reminders

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
    # Pin explicit offsets so the recompute is deterministic.
    await update_general_settings(c, "u1", {"reminder_offsets": ["-2h", "-1h"]})
    try:
        yield c
    finally:
        await close_pool()


def _iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


async def test_reschedule_recomputes_pre_reminders(cfg) -> None:
    t1 = _iso(days=3)
    pre1 = await resolve_pre_reminders(cfg, "u1", reminder_at=t1, explicit=None)
    assert len(pre1) == 2
    task = await task_store.create_task(
        cfg, "u1", "meeting", reminder_at=t1, pre_reminders=pre1
    )

    t2 = _iso(days=5)
    ok = await task_store.update_task(cfg, "u1", task["id"], reminder_at=t2)
    assert ok is True

    fetched = await task_store.get_task(cfg, "u1", task["id"])
    got = set(json.loads(fetched["pre_reminders"]))
    expected = set(await resolve_pre_reminders(cfg, "u1", reminder_at=t2, explicit=None))
    assert got == expected, "pre_reminders were not re-derived for the new time"
    assert got != set(pre1), "pre_reminders stayed pinned to the OLD reminder time"


async def test_reschedule_via_due_date_only_recomputes(cfg) -> None:
    """Moving due_date while a reminder_at is set re-derives against the
    still-current reminder_at (offsets are anchored to reminder_at)."""
    t1 = _iso(days=3)
    task = await task_store.create_task(
        cfg, "u1", "ship it", due_date=_iso(days=3), reminder_at=t1,
        pre_reminders=await resolve_pre_reminders(cfg, "u1", reminder_at=t1, explicit=None),
    )
    ok = await task_store.update_task(cfg, "u1", task["id"], due_date=_iso(days=9))
    assert ok is True
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    # reminder_at unchanged → pre_reminders still resolve to the same 2 entries,
    # but crucially the column was re-written (not left stale/NULL).
    assert fetched["pre_reminders"] is not None
    assert len(json.loads(fetched["pre_reminders"])) == 2


async def test_clearing_reminder_at_collapses_pre_reminders(cfg) -> None:
    t1 = _iso(days=3)
    task = await task_store.create_task(
        cfg, "u1", "cancel alarm", reminder_at=t1,
        pre_reminders=await resolve_pre_reminders(cfg, "u1", reminder_at=t1, explicit=None),
    )
    ok = await task_store.update_task(cfg, "u1", task["id"], reminder_at=None)
    assert ok is True
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert fetched["pre_reminders"] is None


async def test_explicit_pre_reminders_on_update_still_wins(cfg) -> None:
    """Passing pre_reminders explicitly in the same update must NOT be clobbered
    by the auto-recompute."""
    t1 = _iso(days=3)
    task = await task_store.create_task(cfg, "u1", "custom", reminder_at=t1)

    t2 = _iso(days=5)
    custom = await resolve_pre_reminders(cfg, "u1", reminder_at=t2, explicit=["-15m"])
    ok = await task_store.update_task(
        cfg, "u1", task["id"], reminder_at=t2, pre_reminders=custom
    )
    assert ok is True
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert set(json.loads(fetched["pre_reminders"])) == set(custom)
    assert len(custom) == 1


async def test_unrelated_update_leaves_pre_reminders(cfg) -> None:
    """Editing a non-schedule field must not touch pre_reminders (no recompute
    when neither reminder_at nor due_date changes)."""
    t1 = _iso(days=3)
    pre1 = await resolve_pre_reminders(cfg, "u1", reminder_at=t1, explicit=None)
    task = await task_store.create_task(
        cfg, "u1", "note edit", reminder_at=t1, pre_reminders=pre1
    )
    ok = await task_store.update_task(cfg, "u1", task["id"], description="changed")
    assert ok is True
    fetched = await task_store.get_task(cfg, "u1", task["id"])
    assert set(json.loads(fetched["pre_reminders"])) == set(pre1)
