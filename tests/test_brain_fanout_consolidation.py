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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lazyclaw.config import Config
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime.task_runner import (
    TaskRunner,
    _BrainFanoutGroup,
    _FanoutResult,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_runner(
    tmp_path: Path,
    *,
    lane_queue=None,
    consolidator_factory=None,
) -> TaskRunner:
    """Build a TaskRunner skeleton with the heavy deps mocked.

    ``tmp_path`` is required: the runtime's ``_consolidate`` quiet-mode
    branch calls ``get_bg_streaming(self._config, ...)`` which opens
    ``db_session(config)`` and resolves ``config.database_dir /
    "lazyclaw.db"``. A ``MagicMock`` config produces a stringifiable mock
    whose repr ("<MagicMock name='mock.database_dir.__truediv__()' …>")
    becomes a real on-disk filename — see the cleanup of 18 such files
    on 2026-05-16. Using a pytest tmp_path-backed ``Config`` keeps the
    DB write contained to the per-test temp dir.
    """
    runner = TaskRunner.__new__(TaskRunner)
    runner._config = Config(database_dir=tmp_path)
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
    runner._task_caller_depth = {}
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
async def test_consolidate_single_task_falls_back_to_per_task_push(tmp_path):
    """1 task in the group must NOT trigger a synthetic consolidation
    enqueue — it falls back to firing background_done on the original
    callback so single run_background calls keep their existing UX."""
    runner = _make_runner(tmp_path)
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
async def test_consolidate_multi_task_enqueues_one_synthetic_turn(tmp_path):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

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
    # The user cannot see tool output — the reply must restate results
    # in full, never point at "the report above" (2026-08-13 incident).
    assert "user has NOT seen ANY of the results" in synthetic
    assert "NEVER write 'as shown above'" in synthetic
    assert kwargs.get("chat_session_id") == "sess-1"
    # Group cleaned up.
    assert "g2" not in runner._brain_groups


@pytest.mark.asyncio
async def test_consolidate_includes_failed_tasks(tmp_path):
    """Mixed success + failure: synthetic message must surface the
    failure with its error string so the brain can report it."""
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

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
async def test_consolidator_factory_is_used_for_callback(tmp_path):
    """When a factory is wired, the consolidation enqueue receives the
    factory-built callback (Telegram with prefix) — NOT the original
    turn's callback."""
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    consolidator_cb_built = MagicMock(name="consolidator_cb")
    factory = MagicMock(return_value=consolidator_cb_built)
    runner = _make_runner(
        tmp_path, lane_queue=lane_queue, consolidator_factory=factory,
    )

    original_cb = MagicMock(name="original_cb")
    _seed_group(runner, "g4", "u1", [], cb=original_cb)
    runner._brain_groups["g4"].results.extend([
        _FanoutResult(task_id="t1", name="A", success=True, result="r1"),
        _FanoutResult(task_id="t2", name="B", success=True, result="r2"),
    ])

    await runner._consolidate("g4")

    factory.assert_called_once_with("u1", original_cb)
    assert lane_queue.enqueue.await_args.kwargs["callback"] is consolidator_cb_built


# ── Web quiet-mode delivery rescue (2026-06-04 disappearing-reply bug) ──
#
# A Web-UI request that gets AUTO-PROMOTE'd to a background task is
# consolidated by a synthetic turn enqueued on the LANE QUEUE — which
# bypasses the WS request loop (`_run_agent_turn`) that normally flushes the
# terminal "done" frame. With `bg_streaming` OFF the web callback buffers the
# brain's tokens and never sends them, so the consolidated reply is produced
# then SILENTLY DROPPED (and never falls back to Telegram, because the
# origin-aware router picked the live web callback). `_consolidate` must
# re-deliver the reply out-of-band via a `background_done` frame.


class _FakeWebCallback:
    """Duck-typed like ``gateway.routes.chat_ws.WebSocketCallback`` — owns a
    live ``ws`` + ``streaming_state`` and is NOT closed, so
    ``is_live_web_callback`` recognizes it. Also mirrors the real
    ``send_terminal_done`` public method so the streaming-ON terminal path
    (BUG 1) can be duck-typed and asserted."""

    def __init__(self):
        self.ws = MagicMock()
        self.streaming_state = {"bg_streaming": False}
        self.events: list[AgentEvent] = []
        self.terminals: list[str] = []

    async def on_event(self, event: AgentEvent) -> None:
        self.events.append(event)

    async def send_terminal_done(self, content: str) -> None:
        self.terminals.append(content)


class _FakeTelegramNotifier:
    """A non-web callback (no ``ws`` / ``streaming_state``) — must NOT be
    treated as a live web socket by the rescue."""

    def __init__(self):
        self.events: list[AgentEvent] = []

    async def on_event(self, event: AgentEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_consolidate_web_quiet_mode_redelivers_reply(tmp_path, monkeypatch):
    """Web origin + bg_streaming OFF: the synthetic turn's reply must be
    re-delivered out-of-band via a ``background_done`` frame on the web
    callback (otherwise it vanishes — the 2026-06-04 incident)."""
    import lazyclaw.runtime.streaming_setting as ss

    monkeypatch.setattr(ss, "get_bg_streaming", AsyncMock(return_value=False))

    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(
        return_value="You have 3 sheets: Budget, Leads, Invoices.",
    )
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    web_cb = _FakeWebCallback()
    # ONE task — exactly the production shape ("read_all_sheets").
    _seed_group(runner, "gw1", "u1", [], cb=web_cb)
    runner._brain_groups["gw1"].results.append(_FanoutResult(
        task_id="t1", name="read_all_sheets", success=True,
        result="raw sheet dump", duration_ms=80_000,
    ))

    await runner._consolidate("gw1")

    # The synthetic turn was enqueued AND its reply was re-delivered.
    lane_queue.enqueue.assert_awaited_once()
    assert len(web_cb.events) == 1
    ev = web_cb.events[0]
    assert ev.kind == "background_done"
    assert ev.metadata["result"] == "You have 3 sheets: Budget, Leads, Invoices."


@pytest.mark.asyncio
async def test_consolidate_web_streaming_on_no_rescue(tmp_path, monkeypatch):
    """Streaming ON: live tokens already reached the browser during the
    synthetic turn — the rescue must NOT fire (would double-send)."""
    import lazyclaw.runtime.streaming_setting as ss

    monkeypatch.setattr(ss, "get_bg_streaming", AsyncMock(return_value=True))

    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="streamed live")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    web_cb = _FakeWebCallback()
    web_cb.streaming_state = {"bg_streaming": True}
    _seed_group(runner, "gw2", "u1", [], cb=web_cb)
    runner._brain_groups["gw2"].results.extend([
        _FanoutResult(task_id="t1", name="A", success=True, result="r1"),
        _FanoutResult(task_id="t2", name="B", success=True, result="r2"),
    ])

    await runner._consolidate("gw2")

    lane_queue.enqueue.assert_awaited_once()
    assert web_cb.events == []  # no out-of-band background_done rescue


# ── Streaming-ON terminal `done` for the live bubble (BUG 1) ───────────
#
# With bg_streaming ON (default) the reused live web callback streams the
# synthetic consolidation turn's tokens into a chat bubble — but the lane-
# queue path never reaches ``chat_ws._run_one_turn`` which normally flushes
# the terminal ``{"type":"done"}`` frame, so the bubble spins forever.
# ``_consolidate`` must emit a terminal ``done`` (via ``send_terminal_done``)
# to settle it, WITHOUT firing the quiet-mode ``background_done`` rescue.


@pytest.mark.asyncio
async def test_consolidate_web_streaming_on_sends_terminal_done(
    tmp_path, monkeypatch,
):
    """Streaming ON + live web callback: the consolidated reply must settle
    the spinning chat bubble with a terminal ``done`` frame carrying the
    merged text (BUG 1)."""
    import lazyclaw.runtime.streaming_setting as ss

    monkeypatch.setattr(ss, "get_bg_streaming", AsyncMock(return_value=True))

    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="Merged: A=1, B=2.")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    web_cb = _FakeWebCallback()
    web_cb.streaming_state = {"bg_streaming": True}
    _seed_group(runner, "gt1", "u1", [], cb=web_cb)
    runner._brain_groups["gt1"].results.extend([
        _FanoutResult(task_id="t1", name="A", success=True, result="r1"),
        _FanoutResult(task_id="t2", name="B", success=True, result="r2"),
    ])

    await runner._consolidate("gt1")

    lane_queue.enqueue.assert_awaited_once()
    # Terminal `done` frame settles the streaming bubble.
    assert web_cb.terminals == ["Merged: A=1, B=2."]
    # No quiet-mode background_done rescue in streaming mode (no double-send).
    assert web_cb.events == []


@pytest.mark.asyncio
async def test_consolidate_web_quiet_mode_no_terminal_done(
    tmp_path, monkeypatch,
):
    """Quiet-web keeps its ``background_done`` rescue and must NOT also send
    a terminal ``done`` — that path never streamed a live bubble, so a
    duplicate terminal would be wrong."""
    import lazyclaw.runtime.streaming_setting as ss

    monkeypatch.setattr(ss, "get_bg_streaming", AsyncMock(return_value=False))

    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="quiet merged reply")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    web_cb = _FakeWebCallback()  # bg_streaming=False by default
    _seed_group(runner, "gt2", "u1", [], cb=web_cb)
    runner._brain_groups["gt2"].results.extend([
        _FanoutResult(task_id="t1", name="A", success=True, result="r1"),
        _FanoutResult(task_id="t2", name="B", success=True, result="r2"),
    ])

    await runner._consolidate("gt2")

    lane_queue.enqueue.assert_awaited_once()
    # Quiet rescue fired.
    assert len(web_cb.events) == 1
    assert web_cb.events[0].kind == "background_done"
    assert web_cb.events[0].metadata["result"] == "quiet merged reply"
    # No duplicate terminal.
    assert web_cb.terminals == []


@pytest.mark.asyncio
async def test_consolidate_telegram_cb_no_terminal_done(tmp_path, monkeypatch):
    """Non-web (Telegram) callback with streaming ON: has no
    ``send_terminal_done`` and isn't a live web socket — the terminal must
    NOT fire for it (delivered during the turn by its own notifier)."""
    import lazyclaw.runtime.streaming_setting as ss

    monkeypatch.setattr(ss, "get_bg_streaming", AsyncMock(return_value=True))

    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="telegram reply")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    tg_cb = _FakeTelegramNotifier()
    _seed_group(runner, "gt3", "u1", [], cb=tg_cb)
    runner._brain_groups["gt3"].results.extend([
        _FanoutResult(task_id="t1", name="A", success=True, result="r1"),
        _FanoutResult(task_id="t2", name="B", success=True, result="r2"),
    ])

    await runner._consolidate("gt3")

    lane_queue.enqueue.assert_awaited_once()
    assert tg_cb.events == []  # Telegram path unchanged


@pytest.mark.asyncio
async def test_consolidate_telegram_cb_no_rescue(tmp_path, monkeypatch):
    """Non-web (Telegram) callback in quiet mode: delivered during the turn
    by its own notifier — the web rescue must NOT fire for it."""
    import lazyclaw.runtime.streaming_setting as ss

    monkeypatch.setattr(ss, "get_bg_streaming", AsyncMock(return_value=False))

    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="telegram reply")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    tg_cb = _FakeTelegramNotifier()
    _seed_group(runner, "gw3", "u1", [], cb=tg_cb)
    runner._brain_groups["gw3"].results.extend([
        _FanoutResult(task_id="t1", name="A", success=True, result="r1"),
        _FanoutResult(task_id="t2", name="B", success=True, result="r2"),
    ])

    await runner._consolidate("gw3")

    lane_queue.enqueue.assert_awaited_once()
    assert tg_cb.events == []  # Telegram path unchanged, no rescue


@pytest.mark.asyncio
async def test_consolidate_without_lane_queue_does_not_crash(tmp_path):
    """Without lane_queue wired, consolidation must degrade gracefully —
    no exception, group still pruned."""
    runner = _make_runner(tmp_path, lane_queue=None)
    _seed_group(runner, "g5", "u1", [])
    runner._brain_groups["g5"].results.extend([
        _FanoutResult(task_id="t1", name="A", success=True, result="r1"),
        _FanoutResult(task_id="t2", name="B", success=True, result="r2"),
    ])
    await runner._consolidate("g5")
    assert "g5" not in runner._brain_groups


# ── _record_brain_result triggers consolidate when last task settles ──


@pytest.mark.asyncio
async def test_record_brain_result_triggers_consolidate_on_last(tmp_path):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

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


# ── App refresh hint (2026-08-13) ─────────────────────────────────────
# Socket-based delivery (send_terminal_done / background_done rescues)
# requires the ORIGINAL WebSocket to still be open. A phone that locked
# during a long background wait got nothing — the consolidated reply sat
# invisible in history until an app restart. _consolidate must always
# publish a realtime notification frame so clients re-read chat history.


@pytest.mark.asyncio
async def test_consolidate_single_result_emits_chat_refresh_hint(tmp_path):
    from lazyclaw.notifications import realtime

    realtime.clear_user("u-hint1")
    try:
        runner = _make_runner(tmp_path)
        cb = MagicMock()
        cb.on_event = AsyncMock()
        _seed_group(runner, "gh1", "u-hint1", ["t1"], cb=cb)
        runner._brain_groups["gh1"].results.append(_FanoutResult(
            task_id="t1", name="Task 1", success=True, result="hello",
            duration_ms=1_000,
        ))
        runner._brain_groups["gh1"].pending.clear()

        await runner._consolidate("gh1")

        events = realtime.recent_events("u-hint1", limit=5, max_age_s=60)
        assert len(events) == 1
        assert events[0].kind == "chat_reply"
    finally:
        realtime.clear_user("u-hint1")


@pytest.mark.asyncio
async def test_consolidate_multi_result_emits_chat_refresh_hint(tmp_path):
    from lazyclaw.notifications import realtime

    realtime.clear_user("u-hint2")
    try:
        lane_queue = MagicMock()
        lane_queue.enqueue = AsyncMock(return_value="consolidated reply")
        runner = _make_runner(tmp_path, lane_queue=lane_queue)
        _seed_group(runner, "gh2", "u-hint2", [])
        runner._brain_groups["gh2"].results.extend([
            _FanoutResult(task_id="t1", name="A", success=True,
                          result="ra", duration_ms=1_000),
            _FanoutResult(task_id="t2", name="B", success=True,
                          result="rb", duration_ms=1_000),
        ])

        await runner._consolidate("gh2")

        events = realtime.recent_events("u-hint2", limit=5, max_age_s=60)
        assert len(events) == 1
        assert events[0].kind == "chat_reply"
    finally:
        realtime.clear_user("u-hint2")
