"""Regression tests for the 2026-07-30 timezone-correctness pass.

The reported symptom was "notifications don't match Madrid time". The audit
found one canonical convention (reminder_at stored UTC-aware, daemon compares
against a UTC now) violated by several writer paths:

* ``_normalize_reminder_to_utc`` passed AWARE non-UTC strings through
  verbatim, and the daemon's due-check compares ISO STRINGS lexically — a
  ``+02:00`` reminder only matched once the UTC clock string reached its
  wall-clock digits, firing exactly 2h late.
* The REST create route derived ``pre_reminders`` from the RAW client value
  BEFORE ``create_task`` normalized it, so mobile's naive Madrid wall-clock
  was read as UTC and every advance ping fired 2h late.
* The chat skills (``add_task``, ``set_reminder``) stamped naive ISO input
  ``tzinfo=utc`` before the store's normalizer could run.
* ``smart_intake`` serialized deadlines with the user's ``+02:00`` offset.
* Legacy naive rows written before 2026-07-25 were never backfilled.

All tests run against isolated temp DBs / pure functions — never ./data.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.tasks import store as task_store
from lazyclaw.tasks.pre_reminders import parse_iso_datetime
from lazyclaw.tasks.smart_intake import _validate_deadline
from lazyclaw.lazybrain import timezone_util

# Async tests are marked individually — half this suite is pure-function sync.
MADRID = ZoneInfo("Europe/Madrid")


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


# ── _normalize_reminder_to_utc ─────────────────────────────────────────────


def test_normalize_converts_aware_non_utc_to_utc():
    """A '+02:00' string must be re-anchored to '+00:00' (same instant)."""
    out = task_store._normalize_reminder_to_utc(
        "2026-07-31T10:00:00+02:00", "u1"
    )
    assert out == "2026-07-31T08:00:00+00:00"


def test_normalize_is_idempotent_for_utc():
    out = task_store._normalize_reminder_to_utc(
        "2026-07-31T08:00:00+00:00", "u1"
    )
    assert out == "2026-07-31T08:00:00+00:00"


def test_normalize_still_reads_naive_as_user_local(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_DEFAULT_TZ", "Europe/Madrid")
    out = task_store._normalize_reminder_to_utc("2026-07-31T08:00:00", "u1")
    dt = datetime.fromisoformat(out)
    assert dt.utcoffset().total_seconds() == 0
    # 08:00 Madrid (CEST, +2) == 06:00 UTC
    assert dt.hour == 6


# ── parse_iso_datetime honours its "→ UTC-aware" contract ──────────────────


def test_parse_iso_datetime_converts_offset_to_utc():
    dt = parse_iso_datetime("2026-07-31T10:00:00+02:00")
    assert dt == datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
    assert dt.tzinfo == timezone.utc


# ── smart_intake serializes UTC ────────────────────────────────────────────


def test_validate_deadline_serializes_utc():
    out = _validate_deadline("2199-07-31T10:00:00", user_tz=MADRID)
    assert out is not None
    assert out.endswith("+00:00")
    assert datetime.fromisoformat(out) == datetime(
        2199, 7, 31, 8, 0, tzinfo=timezone.utc
    )


# ── settings-tz write-through cache ────────────────────────────────────────


def test_user_tz_prefers_published_settings_zone(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_DEFAULT_TZ", "Europe/Madrid")
    timezone_util.remember_user_tz("cache-user", "America/New_York")
    try:
        assert str(timezone_util.user_tz("cache-user")) == "America/New_York"
        # Other users (and None) still resolve the env default.
        assert str(timezone_util.user_tz("other-user")) == "Europe/Madrid"
        assert str(timezone_util.user_tz(None)) == "Europe/Madrid"
    finally:
        timezone_util.remember_user_tz("cache-user", None)


def test_remember_user_tz_rejects_invalid_zone():
    timezone_util.remember_user_tz("cache-user", "Not/AZone")
    assert "cache-user" not in timezone_util._SETTINGS_TZ


# ── REST create route derives pre_reminders from the NORMALIZED value ──────


@pytest.mark.asyncio
async def test_create_route_derives_pre_reminders_from_normalized(cfg, monkeypatch):
    """Naive Madrid 08:00 + offsets [-2h,-1h] → pings at 04:00Z/05:00Z.

    Before the fix the derivation read the raw naive value as 08:00 UTC and
    produced 06:00Z/07:00Z — the "2h before" ping landed exactly AT the
    normalized reminder instant (06:00Z == 08:00 Madrid).
    """
    monkeypatch.setenv("LAZYCLAW_DEFAULT_TZ", "Europe/Madrid")
    from lazyclaw.gateway.routes import tasks as tasks_route

    captured = {}

    async def _capture_resolve(config, user_id, *, reminder_at, due_date=None, explicit):
        captured["reminder_at"] = reminder_at
        return []

    monkeypatch.setattr(tasks_route, "resolve_pre_reminders", _capture_resolve)
    monkeypatch.setattr(tasks_route, "_config", cfg)

    class _Body:
        title = "medicines"
        description = None
        category = None
        priority = "medium"
        due_date = "2199-07-31"
        reminder_at = "2199-07-31T08:00:00"  # naive Madrid wall-clock
        recurring = None
        recur_until = None
        tags = None
        steps = None
        pre_reminders = None
        id = None

        @staticmethod
        def model_dump(**_kw):
            return {"title": "medicines"}

    class _User:
        id = "u1"

    result = await tasks_route.create_task_route.__wrapped__(_Body(), _User()) \
        if hasattr(tasks_route.create_task_route, "__wrapped__") else None
    if result is None:
        # FastAPI keeps the original coroutine accessible via the endpoint
        # function itself — call it directly (Depends default is bypassed by
        # passing user explicitly).
        result = await tasks_route.create_task_route(_Body(), _User())

    # The derivation must have received the UTC-normalized instant, not the
    # raw naive string.
    got = datetime.fromisoformat(captured["reminder_at"])
    assert got == datetime(2199, 7, 31, 6, 0, tzinfo=timezone.utc)
    # And the stored task carries the same normalized instant.
    stored = datetime.fromisoformat(result["task"]["reminder_at"])
    assert stored == got


# ── chat add_task: naive ISO is the USER's wall-clock ──────────────────────


@pytest.mark.asyncio
async def test_add_task_skill_naive_is_user_local(cfg, monkeypatch):
    monkeypatch.setenv("LAZYCLAW_DEFAULT_TZ", "Europe/Madrid")
    from lazyclaw.skills.builtin.task_manager import AddTaskSkill

    skill = AddTaskSkill(cfg)
    out = await skill.execute(
        "u1", {"title": "take pill", "reminder_at": "2199-07-30T21:00:00"}
    )
    assert "past" not in out.lower()
    tasks = await task_store.list_tasks(cfg, "u1")
    match = [t for t in tasks if t["title"] == "take pill"]
    assert match, f"task not created: {out}"
    stored = datetime.fromisoformat(match[0]["reminder_at"])
    # 21:00 Madrid (CEST) == 19:00 UTC — the old code stored 21:00 UTC.
    assert stored == datetime(2199, 7, 30, 19, 0, tzinfo=timezone.utc)


# ── legacy naive-row backfill in init_db ───────────────────────────────────


@pytest.mark.asyncio
async def test_init_db_backfills_naive_reminders(cfg, monkeypatch):
    monkeypatch.setenv("LAZYCLAW_DEFAULT_TZ", "Europe/Madrid")
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO tasks (id, user_id, title, status, reminder_at, "
            "pre_reminders, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (
                "legacy1", "u1", "enc-title", "todo",
                "2199-08-02T08:00:00",  # naive = 08:00 Madrid
                json.dumps(["2199-08-02T06:00:00+00:00"]),  # derived off the mis-read base
            ),
        )
        await db.commit()

    await init_db(cfg)  # re-run: backfill is part of init and idempotent

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT reminder_at, pre_reminders FROM tasks WHERE id = 'legacy1'"
        )
        rem, pre = await cur.fetchone()
    fixed = datetime.fromisoformat(rem)
    # 08:00 Madrid (Aug = CEST) == 06:00 UTC, now stored aware.
    assert fixed == datetime(2199, 8, 2, 6, 0, tzinfo=timezone.utc)
    # The advance ping shifted by the same -2h delta.
    entries = [datetime.fromisoformat(e) for e in json.loads(pre)]
    assert entries == [datetime(2199, 8, 2, 4, 0, tzinfo=timezone.utc)]

    # Idempotent: a second init_db leaves the aware row untouched.
    await init_db(cfg)
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT reminder_at FROM tasks WHERE id = 'legacy1'"
        )
        (rem2,) = await cur.fetchone()
    assert rem2 == rem
