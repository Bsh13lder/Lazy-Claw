"""Recurring-respawn SCHEDULING defects (audit 2026-07-27).

Sibling of ``test_recurring_carry_forward.py`` (which covers *what data* the
respawn carries). This file covers *when* the respawned occurrence lands and
*what happens when the respawn breaks*.

D1 — the respawn always wrote a DATE-ONLY ``due_date``
     (``next_local.date().isoformat()``), silently dropping the time-of-day the
     user set. Downstream that is not cosmetic: the phone's
     ``reminderBaseTime`` (``mobile/lib/core/notifications/task_reminder_service.dart``)
     anchors every advance-reminder offset on a timed due when there is one and
     falls back to "09:00 on that date" when the due is date-only — so a 18:30
     task respawned date-only slid every offset by 9.5h.

D2 — ``get_next_run`` returns the next CRON instant, but the respawn overwrites
     its time-of-day with the ORIGINAL occurrence's. Completing a "cron 09:00 /
     reminder 08:00" task at 08:50 therefore respawned a reminder for *today
     08:00* — already past. The duplicate-looking task then nagged within one
     heartbeat tick. The respawn must skip forward to a cron occurrence whose
     reminder is strictly in the future.

D4 — the whole respawn sat inside ``except Exception: logger.warning(...)``.
     The task is already flipped to ``done`` by then and the idempotency guard
     (``if status == "done": return True``) means a retry can never respawn it,
     so a single transient failure killed the series silently behind a 200.

D5 — ``complete_task`` read status via ``get_task`` and *then* UPDATEd. Two
     concurrent completes (mobile's at-least-once retry racing a web click)
     both passed the guard and both respawned → duplicate next occurrences.

D6 — the respawn's "keep the reminder naive" branch was dead code:
     ``create_task`` runs ``_normalize_reminder_to_utc`` two calls later, so the
     stored value is ALWAYS UTC-aware. The long comment documented a guarantee
     the code no longer provided.

Store-level, isolated temp DB (never the live ``./data``).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain.timezone_util import user_tz
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


def _fake_daily_cron(monkeypatch, hour: int, minute: int, now: datetime | None = None):
    """Pin ``get_next_run`` to a deterministic DAILY grid at ``hour:minute`` local.

    Unlike the ``far_future_cron`` fixture next door this honours ``after``, so
    the "advance until the reminder is in the future" walk (D2) can actually
    step. Returns UTC-aware instants, exactly like the real implementation.

    ``now`` pins the grid's origin for the ``after=None`` (first) call — the real
    ``get_next_run`` reads the wall clock there, so a test that pins the store
    helper's ``now`` must pin the cron's too or the two disagree by weeks.
    """
    tz = user_tz(None)

    def _fake_get_next_run(expr, after=None, user_id=None):  # noqa: ARG001
        origin = (now or datetime.now(timezone.utc)).astimezone(tz)
        base = origin if after is None else after.astimezone(tz)
        candidate = base.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    monkeypatch.setattr("lazyclaw.heartbeat.cron.get_next_run", _fake_get_next_run)
    return _fake_get_next_run


def _open_tasks(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if t["status"] != "done"]


def _as_local(value: str) -> datetime:
    """Parse a stored timestamp the way the phone does: naive = already local."""
    tz = user_tz(None)
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


# ---------------------------------------------------------------------------
# D1 — a timed due_date must respawn timed
# ---------------------------------------------------------------------------

async def test_respawn_carries_timed_due_date_time_of_day(cfg, monkeypatch) -> None:
    """A ``…T18:30:00`` due must come back timed at 18:30, not date-only.

    The respawn hard-coded ``next_local.date().isoformat()``, so every cycle
    after the first lost the time-of-day and the phone re-anchored the user's
    advance-reminder offsets on its 09:00 date-only default.
    """
    _fake_daily_cron(monkeypatch, 9, 0)
    tz = user_tz(None)
    timed_due = (
        (datetime.now(tz) + timedelta(days=1))
        .replace(hour=18, minute=30, second=0, microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%S")
    )

    task = await task_store.create_task(
        cfg, "u1", "gym", recurring="0 9 * * *", due_date=timed_due,
    )
    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    nxt_due = spawned[0]["due_date"]
    assert nxt_due and "T" in nxt_due, (
        "respawn downgraded a TIMED due_date to date-only — the phone now "
        f"anchors every reminder offset on its 09:00 default (got {nxt_due!r})"
    )
    local = _as_local(nxt_due)
    assert (local.hour, local.minute) == (18, 30), (
        f"respawned due_date lost its 18:30 wall-clock (got {nxt_due!r})"
    )


async def test_respawn_keeps_date_only_due_date_date_only(cfg, monkeypatch) -> None:
    """The mirror case: a date-only due must NOT gain a spurious time component
    (mobile renders a timed due with a clock chip)."""
    _fake_daily_cron(monkeypatch, 9, 0)
    task = await task_store.create_task(
        cfg, "u1", "stretch", recurring="0 9 * * *", due_date="2026-07-27",
    )
    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    nxt_due = spawned[0]["due_date"]
    assert nxt_due and "T" not in nxt_due, (
        f"date-only due_date grew a time component on respawn: {nxt_due!r}"
    )


# ---------------------------------------------------------------------------
# D2 — the respawned reminder must be strictly in the future
# ---------------------------------------------------------------------------

async def test_next_occurrence_advances_past_a_stale_reminder(monkeypatch) -> None:
    """Pure-helper proof, pinned ``now``: when projecting the original's
    time-of-day onto the next cron day lands in the PAST, the respawn must step
    to the FOLLOWING cron occurrence.

    Scenario = the reported one: cron fires 09:00 daily, the user's reminder is
    a 08:00 lead, and the task is completed at 08:50. The naive projection gives
    *today 08:00* — 50 minutes ago — so the phone gets a duplicate-looking task
    that nags on the very next heartbeat tick.
    """
    tz = user_tz(None)
    # Pin "now" to 08:50 local on a fixed day (DST-stable date, mid-month).
    now_local = datetime(2026, 7, 15, 8, 50, tzinfo=tz)
    _fake_daily_cron(monkeypatch, 9, 0, now=now_local)

    due, reminder = task_store._next_occurrence_fields(
        "0 9 * * *", None,
        orig_due=None,
        orig_reminder="2026-07-14T08:00:00",
        now=now_local.astimezone(timezone.utc),
    )

    assert reminder is not None
    assert _as_local(reminder) > now_local, (
        "respawned reminder lands in the PAST — a duplicate task appears for "
        f"today and nags within one heartbeat tick (got {reminder!r})"
    )
    # Time-of-day is preserved; only the DATE rolls forward.
    assert (_as_local(reminder).hour, _as_local(reminder).minute) == (8, 0)
    assert _as_local(reminder).date() == datetime(2026, 7, 16).date()
    assert due == "2026-07-16", f"due_date must track the advanced cron day, got {due!r}"


async def test_next_occurrence_uses_first_cron_hit_when_already_future(monkeypatch) -> None:
    """The advance walk must NOT skip a perfectly good occurrence — completing
    the 09:00 daily at 08:50 with a 09:00 reminder still respawns for TODAY."""
    tz = user_tz(None)
    now_local = datetime(2026, 7, 15, 8, 50, tzinfo=tz)
    _fake_daily_cron(monkeypatch, 9, 0, now=now_local)

    due, reminder = task_store._next_occurrence_fields(
        "0 9 * * *", None,
        orig_due=None,
        orig_reminder="2026-07-14T09:00:00",
        now=now_local.astimezone(timezone.utc),
    )
    assert due == "2026-07-15"
    assert _as_local(reminder).date() == datetime(2026, 7, 15).date()


async def test_next_occurrence_caps_the_advance_walk(monkeypatch) -> None:
    """A cron stub that never moves must not spin forever — the walk is capped
    and the failure surfaces (D4 records it) instead of hanging the request."""
    frozen = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
    calls = {"n": 0}

    def _stuck(expr, after=None, user_id=None):  # noqa: ARG001
        calls["n"] += 1
        return frozen

    monkeypatch.setattr("lazyclaw.heartbeat.cron.get_next_run", _stuck)

    with pytest.raises(RuntimeError, match="advance"):
        task_store._next_occurrence_fields(
            "0 9 * * *", None,
            orig_due=None,
            orig_reminder="2026-07-14T08:00:00",
            now=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
        )
    assert calls["n"] == task_store._RESPAWN_MAX_ADVANCES


async def test_respawned_row_reminder_is_in_the_future(cfg, monkeypatch) -> None:
    """End-to-end through ``complete_task``: the persisted row must never carry
    an already-past reminder.

    Cron pinned to 23:59 local with a 00:01 reminder time-of-day, so the naive
    projection onto the first cron day is ~24h in the past and the walk MUST
    advance one day.
    """
    _fake_daily_cron(monkeypatch, 23, 59)
    task = await task_store.create_task(
        cfg, "u1", "night pill",
        recurring="59 23 * * *",
        reminder_at="2026-07-14T00:01:00",
    )
    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    rem = spawned[0]["reminder_at"]
    assert rem, "respawn lost its reminder"
    assert _as_local(rem) > datetime.now(user_tz(None)), (
        f"respawned reminder is already past: {rem!r}"
    )
    assert (_as_local(rem).hour, _as_local(rem).minute) == (0, 1), (
        f"the advance walk must not change the wall-clock time-of-day: {rem!r}"
    )


# ---------------------------------------------------------------------------
# D4 — a failed respawn must be recorded + surfaced, never swallowed
# ---------------------------------------------------------------------------

async def test_respawn_failure_is_recorded_on_the_task(cfg, monkeypatch) -> None:
    """When the respawn throws, the task is already ``done`` and the idempotency
    guard makes a retry impossible — so the failure MUST be recorded on the row
    (``last_error``) and pushed through the notification spine. Otherwise the
    recurring series just dies behind a 200."""
    def _boom(expr, after=None, user_id=None):  # noqa: ARG001
        raise RuntimeError("cron backend exploded")

    monkeypatch.setattr("lazyclaw.heartbeat.cron.get_next_run", _boom)

    seen: list[dict] = []

    async def _fake_notify(config, user_id, **kw):  # noqa: ARG001
        seen.append(kw)
        return {}

    monkeypatch.setattr("lazyclaw.notifications.spine.notify", _fake_notify)

    task = await task_store.create_task(
        cfg, "u1", "weekly review", recurring="0 9 * * 5",
    )
    # complete_task must NOT raise — a failed respawn can't un-complete a task.
    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    done = await task_store.get_task(cfg, "u1", task["id"])
    assert done is not None
    assert done["status"] == "done"
    assert done["last_error"], (
        "a failed recurring respawn left NO trace on the task — the series "
        "dies silently and nothing can retry it"
    )
    assert "respawn" in done["last_error"].lower()
    assert seen, "a failed respawn emitted no durable notification"
    assert seen[0]["severity"] in {"important", "urgent"}


async def test_respawn_failure_does_not_block_completion_of_plain_tasks(
    cfg, monkeypatch
) -> None:
    """Sanity: the failure bookkeeping is respawn-scoped — a non-recurring
    completion never touches ``last_error``."""
    def _boom(expr, after=None, user_id=None):  # noqa: ARG001
        raise RuntimeError("should never be called")

    monkeypatch.setattr("lazyclaw.heartbeat.cron.get_next_run", _boom)

    task = await task_store.create_task(cfg, "u1", "one-off")
    assert await task_store.complete_task(cfg, "u1", task["id"]) is True
    done = await task_store.get_task(cfg, "u1", task["id"])
    assert done is not None
    assert done["last_error"] is None


# ---------------------------------------------------------------------------
# D5 — the status flip must be atomic
# ---------------------------------------------------------------------------

async def test_concurrent_completes_spawn_exactly_one_occurrence(
    cfg, monkeypatch
) -> None:
    """Two overlapping completes must produce ONE next occurrence.

    ``complete_task`` read the status with ``get_task`` and only then UPDATEd,
    so the mobile client's at-least-once retry racing a web click had both
    callers pass the ``status == "done"`` guard and both run the respawn. The
    flip has to be a conditional UPDATE with the respawn gated on rowcount.
    """
    _fake_daily_cron(monkeypatch, 9, 0)
    task = await task_store.create_task(
        cfg, "u1", "water the plants", recurring="0 9 * * *",
    )

    results = await asyncio.gather(
        task_store.complete_task(cfg, "u1", task["id"]),
        task_store.complete_task(cfg, "u1", task["id"]),
    )
    # Return contract unchanged: completing an already-done task returns True.
    assert results == [True, True]

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1, (
        "concurrent completes spawned DUPLICATE next occurrences "
        f"({len(spawned)} open rows) — the status flip is not atomic"
    )


async def test_complete_missing_task_still_returns_false(cfg) -> None:
    """The conditional UPDATE must not turn a missing task into a success."""
    assert await task_store.complete_task(cfg, "u1", "no-such-id") is False


# ---------------------------------------------------------------------------
# D6 — document the ACTUAL stored shape (the "keep naive" branch was dead)
# ---------------------------------------------------------------------------

async def test_respawned_naive_reminder_is_stored_utc_aware(cfg, monkeypatch) -> None:
    """A naive original respawns as a UTC-AWARE row.

    The respawn's old comment claimed a naive original "stays naive"; it never
    did — ``create_task`` normalises it two calls later. This test pins the real
    contract (same local wall-clock, canonical UTC-aware storage) so the
    comment and the code can't drift apart again.
    """
    _fake_daily_cron(monkeypatch, 8, 0)
    tz = user_tz(None)
    naive_eight = (
        (datetime.now(tz) + timedelta(days=1))
        .replace(hour=8, minute=0, second=0, microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%S")
    )
    task = await task_store.create_task(
        cfg, "u1", "meds", recurring="0 8 * * *", reminder_at=naive_eight,
    )
    assert await task_store.complete_task(cfg, "u1", task["id"]) is True

    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    rem = spawned[0]["reminder_at"]
    parsed = datetime.fromisoformat(rem)
    assert parsed.tzinfo is not None, (
        f"respawned reminder should be canonical UTC-aware, got {rem!r}"
    )
    assert (parsed.astimezone(tz).hour, parsed.astimezone(tz).minute) == (8, 0)


async def test_respawn_with_unparseable_reminder_still_spawns(cfg, monkeypatch) -> None:
    """A corrupt ``reminder_at`` must degrade to "no reminder", not kill the
    series (the row still has to come back so the user keeps the habit)."""
    _fake_daily_cron(monkeypatch, 9, 0)
    task = await task_store.create_task(cfg, "u1", "odd", recurring="0 9 * * *")
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE tasks SET reminder_at = ? WHERE id = ?",
            ("not-a-timestamp", task["id"]),
        )
        await db.commit()

    assert await task_store.complete_task(cfg, "u1", task["id"]) is True
    spawned = _open_tasks(await task_store.list_tasks(cfg, "u1"))
    assert len(spawned) == 1
    assert spawned[0]["reminder_at"] is None
    assert not json.loads(spawned[0]["pre_reminders"] or "[]")
