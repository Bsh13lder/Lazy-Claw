"""Brain fan-out consolidation — TaskRunner buckets sibling brain-spawned
``run_background`` tasks under a shared ``fanout_group_id`` and emits
ONE synthetic consolidation turn (via ``lane_queue.enqueue``) when the
last sibling settles, instead of N "✅ Background task done" pushes.

Pins the architectural rule the user stated: "all workers report back to
the main brain, then the brain tells me ONE accurate summary."

Production logs (2026-04-28 18:33-18:38) showed 3 separate
``run_background`` calls each producing its own Telegram push because
``RunBackgroundSkill._callback`` was never set per turn AND there was no
notion of a fan-out group. These tests pin the fix.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime.task_runner import (
    TaskRunner,
    _BrainFanoutGroup,
    _FanoutResult,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_runner(*, lane_queue=None, consolidator_factory=None) -> TaskRunner:
    """Build a TaskRunner skeleton with the heavy deps mocked."""
    runner = TaskRunner.__new__(TaskRunner)
    runner._config = MagicMock()
    runner._router = MagicMock()
    runner._registry = MagicMock()
    runner._eco_router = MagicMock()
    runner._permission_checker = None
    runner._default_callback = None
    runner._team_lead = None
    runner._lane_queue = lane_queue
    runner._consolidator_factory = consolidator_factory
    runner._running = {}
    runner._task_users = {}
    runner._task_names = {}
    runner._task_starts = {}
    runner._task_provenance = {}
    runner._brain_groups = {}
    return runner


def _seed_group(
    runner: TaskRunner,
    group_id: str,
    user_id: str,
    task_ids: list[str],
    *,
    cb=None,
    chat_session_id: str | None = None,
) -> _BrainFanoutGroup:
    group = _BrainFanoutGroup(
        group_id=group_id,
        user_id=user_id,
        pending=set(task_ids),
        consolidator_cb=cb,
        chat_session_id=chat_session_id,
    )
    runner._brain_groups[group_id] = group
    return group


# ── Single-task degraded path ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_consolidate_single_task_falls_back_to_per_task_push():
    """1 task in the group must NOT trigger a synthetic consolidation
    enqueue — it falls back to firing background_done on the original
    callback so single run_background calls keep their existing UX."""
    runner = _make_runner()
    cb = MagicMock()
    cb.on_event = AsyncMock()
    _seed_group(runner, "g1", "u1", ["t1"], cb=cb)
    runner._brain_groups["g1"].results.append(_FanoutResult(
        task_id="t1", name="Task 1", success=True, result="hello",
        duration_ms=12_000,
    ))
    # All pending settled (none, since we only seeded one task and put
    # its result in directly).
    runner._brain_groups["g1"].pending.clear()

    await runner._consolidate("g1")

    # No lane_queue enqueue (none configured anyway).
    # The original callback got the per-task done event.
    cb.on_event.assert_awaited_once()
    fired_event = cb.on_event.await_args.args[0]
    assert fired_event.kind == "background_done"
    assert fired_event.metadata["result"] == "hello"
    # Group should be removed.
    assert "g1" not in runner._brain_groups


# ── Multi-task consolidation: synthetic enqueue ───────────────────────


@pytest.mark.asyncio
async def test_consolidate_multi_task_enqueues_one_synthetic_turn():
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(lane_queue=lane_queue)

    _seed_group(runner, "g2", "u1", [], chat_session_id="sess-1")
    g = runner._brain_groups["g2"]
    g.results.extend([
        _FanoutResult(task_id="t1", name="Salon A", success=True,
                      result="email_a@x.com", duration_ms=10_000),
        _FanoutResult(task_id="t2", name="Salon B", success=True,
                      result="email_b@x.com", duration_ms=11_000),
        _FanoutResult(task_id="t3", name="Salon C", success=True,
                      result="email_c@x.com", duration_ms=12_000),
    ])

    await runner._consolidate("g2")

    lane_queue.enqueue.assert_awaited_once()
    args, kwargs = lane_queue.enqueue.await_args
    assert args[0] == "u1"
    synthetic = args[1]
    # Synthetic instruction must list every result and instruct the brain
    # to write ONE consolidated summary.
    assert "Background fan-out complete" in synthetic
    assert "Salon A" in synthetic
    assert "Salon B" in synthetic
    assert "Salon C" in synthetic
    assert "email_a@x.com" in synthetic
    assert "ONE consolidated summary" in synthetic
    assert kwargs.get("chat_session_id") == "sess-1"
    # Group cleaned up.
    assert "g2" not in runner._brain_groups


@pytest.mark.asyncio
async def test_consolidate_includes_failed_tasks():
    """Mixed success + failure: synthetic message must surface the
    failure with its error string so the brain can report it."""
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(lane_queue=lane_queue)

    _seed_group(runner, "g3", "u1", [])
    runner._brain_groups["g3"].results.extend([
        _FanoutResult(task_id="t1", name="Salon A", success=True,
                      result="email_a@x.com", duration_ms=8_000),
        _FanoutResult(task_id="t2", name="Salon B", success=False,
                      error="Timed out after 120s"),
    ])

    await runner._consolidate("g3")

    synthetic = lane_queue.enqueue.await_args.args[1]
    assert "Salon A" in synthetic
    assert "email_a@x.com" in synthetic
    assert "Salon B" in synthetic
    assert "FAILED" in synthetic
    assert "Timed out after 120s" in synthetic


@pytest.mark.asyncio
async def test_consolidator_factory_is_used_for_callback():
    """When a factory is wired, the consolidation enqueue receives the
    factory-built callback (Telegram with prefix) — NOT the original
    turn's callback."""
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    consolidator_cb_built = MagicMock(name="consolidator_cb")
    factory = MagicMock(return_value=consolidator_cb_built)
    runner = _make_runner(lane_queue=lane_queue, consolidator_factory=factory)

    original_cb = MagicMock(name="original_cb")
    _seed_group(runner, "g4", "u1", [], cb=original_cb)
    runner._brain_groups["g4"].results.extend([
        _FanoutResult(task_id="t1", name="A", success=True, result="r1"),
        _FanoutResult(task_id="t2", name="B", success=True, result="r2"),
    ])

    await runner._consolidate("g4")

    factory.assert_called_once_with("u1", original_cb)
    assert lane_queue.enqueue.await_args.kwargs["callback"] is consolidator_cb_built


@pytest.mark.asyncio
async def test_consolidate_without_lane_queue_does_not_crash():
    """Without lane_queue wired, consolidation must degrade gracefully —
    no exception, group still pruned."""
    runner = _make_runner(lane_queue=None)
    _seed_group(runner, "g5", "u1", [])
    runner._brain_groups["g5"].results.extend([
        _FanoutResult(task_id="t1", name="A", success=True, result="r1"),
        _FanoutResult(task_id="t2", name="B", success=True, result="r2"),
    ])
    await runner._consolidate("g5")
    assert "g5" not in runner._brain_groups


# ── _record_brain_result triggers consolidate when last task settles ──


@pytest.mark.asyncio
async def test_record_brain_result_triggers_consolidate_on_last():
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(lane_queue=lane_queue)

    g = _seed_group(runner, "g6", "u1", ["t1", "t2"])

    # First task settles — no consolidate yet.
    runner._record_brain_result(_FanoutResult(
        task_id="t1", name="A", success=True, result="r1",
    ))
    await asyncio.sleep(0)  # let any scheduled tasks run
    lane_queue.enqueue.assert_not_called()
    assert g.pending == {"t2"}

    # Second (last) settles — consolidate fires (scheduled via create_task).
    runner._record_brain_result(_FanoutResult(
        task_id="t2", name="B", success=True, result="r2",
    ))
    # Give the create_task a chance to run.
    await asyncio.sleep(0.01)
    lane_queue.enqueue.assert_awaited_once()


# ── source="brain" suppresses TelegramNotifier per-task push ──────────


def test_telegram_notifier_skips_brain_source_done():
    from lazyclaw.notifications.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(
        bot=MagicMock(),
        admin_chat_id_fn=lambda: "123",
    )
    event = AgentEvent(
        kind="background_done",
        detail="Background task 'X' completed",
        metadata={"name": "X", "result": "ok", "source": "brain"},
    )
    text, parse_mode = notifier._format(event)
    assert text is None
    assert parse_mode is None


def test_telegram_notifier_skips_brain_source_failed():
    from lazyclaw.notifications.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(
        bot=MagicMock(),
        admin_chat_id_fn=lambda: "123",
    )
    event = AgentEvent(
        kind="background_failed",
        detail="Background task 'X' failed",
        metadata={"name": "X", "error": "kaboom", "source": "brain"},
    )
    text, parse_mode = notifier._format(event)
    assert text is None
    assert parse_mode is None


def test_telegram_notifier_keeps_user_source():
    """source != 'brain' → existing per-task push remains intact."""
    from lazyclaw.notifications.telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(
        bot=MagicMock(),
        admin_chat_id_fn=lambda: "123",
    )
    event = AgentEvent(
        kind="background_done",
        detail="Background task 'X' completed",
        metadata={"name": "Cron Job", "result": "all good", "source": "cron"},
    )
    text, parse_mode = notifier._format(event)
    assert text is not None
    assert "Cron Job" in text
    assert parse_mode == "HTML"
