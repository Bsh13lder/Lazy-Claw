"""RC2 — dispatch_subagents results consolidate into ONE brain reply.

2026-05-29 "Chek my whats up" incident: `dispatch_subagents` was
fire-and-forget with NO auto-consolidation turn (unlike `run_background`).
Subagent results routed to `pending_subagent_notes` that only drain on the
NEXT user message, so the brain's "I'll fold results into my next reply"
promise was never fulfilled — the user got a promise and no data.

The fix reuses TaskRunner's proven brain-fan-out machinery:
`register_subagent_fanout` buckets the dispatch's subagents under a shared
group; `record_subagent_result` feeds each settled subagent in; when the
last settles, the existing `_consolidate` enqueues ONE synthetic brain
turn on the lane queue. The AgentDispatcher gains `on_register` / `on_settle`
hooks + a `fanout_group_id` tag on the terminal bus event so the chat WS
pump can drop the per-subagent side-note (the consolidator owns delivery).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lazyclaw.config import Config
from lazyclaw.runtime.task_runner import TaskRunner


def _make_runner(tmp_path: Path, *, lane_queue=None) -> TaskRunner:
    runner = TaskRunner.__new__(TaskRunner)
    runner._config = Config(database_dir=tmp_path)
    runner._router = MagicMock()
    runner._registry = MagicMock()
    runner._eco_router = MagicMock()
    runner._permission_checker = None
    runner._default_callback = None
    runner._team_lead = None
    runner._lane_queue = lane_queue
    runner._consolidator_factory = None
    runner._running = {}
    runner._task_users = {}
    runner._task_names = {}
    runner._task_starts = {}
    runner._task_provenance = {}
    runner._task_caller_depth = {}
    runner._brain_groups = {}
    return runner


# ── task_runner public methods ────────────────────────────────────────


def test_register_subagent_fanout_creates_group(tmp_path):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock()
    runner = _make_runner(tmp_path, lane_queue=lane_queue)

    ok = runner.register_subagent_fanout(
        "grp1", "u1", ["subagent-a", "subagent-b"], cb := MagicMock(),
        chat_session_id="sess",
    )
    assert ok is True
    grp = runner._brain_groups["grp1"]
    assert grp.pending == {"subagent-a", "subagent-b"}
    assert grp.consolidator_cb is cb
    assert grp.chat_session_id == "sess"


def test_register_subagent_fanout_noop_without_lane_queue(tmp_path):
    runner = _make_runner(tmp_path, lane_queue=None)
    ok = runner.register_subagent_fanout("grp", "u1", ["subagent-a"], None)
    assert ok is False
    assert "grp" not in runner._brain_groups


@pytest.mark.asyncio
async def test_record_subagent_result_consolidates_on_last(tmp_path):
    lane_queue = MagicMock()
    lane_queue.enqueue = AsyncMock(return_value="ok")
    runner = _make_runner(tmp_path, lane_queue=lane_queue)
    runner.register_subagent_fanout(
        "grp2", "u1", ["subagent-a", "subagent-b"], MagicMock(),
        chat_session_id="sess2",
    )

    runner.record_subagent_result(
        "subagent-a", name="explore subagent", success=True,
        result="WhatsApp: 3 unread", duration_ms=900,
    )
    await asyncio.sleep(0)
    lane_queue.enqueue.assert_not_called()  # one still pending

    runner.record_subagent_result(
        "subagent-b", name="explore subagent", success=True,
        result="Email: 5 unread", duration_ms=1100,
    )
    await asyncio.sleep(0.02)  # let create_task(_consolidate) run

    lane_queue.enqueue.assert_awaited_once()
    synthetic = lane_queue.enqueue.await_args.args[1]
    assert "WhatsApp: 3 unread" in synthetic
    assert "Email: 5 unread" in synthetic
    assert "ONE consolidated summary" in synthetic
    assert lane_queue.enqueue.await_args.kwargs.get("chat_session_id") == "sess2"


# ── AgentDispatcher hooks ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_async_invokes_register_and_settle_hooks(monkeypatch, tmp_path):
    from lazyclaw.runtime import dispatcher as disp_mod
    from lazyclaw.runtime.dispatcher import (
        AgentDispatcher, AgentType, SubagentConfig, SubagentResult,
    )
    from lazyclaw.runtime import task_event_bus

    dispatcher = AgentDispatcher(
        config=MagicMock(), eco_router=MagicMock(), registry=MagicMock(),
        permission_checker=None, team_lead=None, callback=None,
    )

    async def _fake_run(cfg, user_id, task_id_override=None):
        return SubagentResult(
            agent_type=cfg.agent_type, task=cfg.task, result="done",
            success=True, tokens_used=0, duration_ms=10,
        )

    monkeypatch.setattr(dispatcher, "_run_subagent", _fake_run)

    published: list = []
    monkeypatch.setattr(task_event_bus, "publish", lambda ev: published.append(ev))

    registered: list = []
    settled: list = []

    configs = [
        SubagentConfig(AgentType.EXPLORE, "check whatsapp"),
        SubagentConfig(AgentType.EXPLORE, "check email"),
    ]

    task_ids = await dispatcher.submit_async(
        configs, "u1",
        on_register=lambda ids: registered.append(list(ids)),
        on_settle=lambda tid, res: settled.append((tid, res.success)),
        fanout_group_id="grpX",
    )

    # on_register fired ONCE with the full task-id list, BEFORE any settle.
    assert registered == [task_ids]
    # Let the spawned subagent tasks run to completion.
    await asyncio.sleep(0.05)
    assert sorted(t for t, _ in settled) == sorted(task_ids)
    # Terminal bus events carry the fanout_group_id so the chat WS pump
    # can drop the per-subagent side-note (consolidator owns delivery).
    assert published, "expected terminal bus events"
    assert all(getattr(ev, "fanout_group_id", None) == "grpX" for ev in published)


# ── dispatch_subagents skill wires consolidation when runner present ──


@pytest.mark.asyncio
async def test_skill_registers_fanout_when_runner_has_lane_queue(monkeypatch):
    from lazyclaw.skills.builtin import dispatch as dispatch_mod
    from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill

    fake_runner = MagicMock()
    fake_runner._lane_queue = MagicMock()  # lane queue present → consolidate
    fake_runner.register_subagent_fanout = MagicMock(return_value=True)
    fake_runner.record_subagent_result = MagicMock()

    captured = {}

    class _FakeDispatcher:
        def __init__(self, **kwargs):
            pass

        async def submit_async(self, configs, user_id, on_register=None,
                               on_settle=None, fanout_group_id=None):
            captured["fanout_group_id"] = fanout_group_id
            captured["on_register"] = on_register
            ids = [f"subagent-{i}" for i in range(len(configs))]
            if on_register:
                on_register(ids)
            return ids

    monkeypatch.setattr(dispatch_mod, "AgentDispatcher", _FakeDispatcher)

    skill = DispatchSubagentsSkill(
        config=MagicMock(), registry=MagicMock(), eco_router=MagicMock(),
        permission_checker=None, callback=MagicMock(), team_lead=None,
        task_runner=fake_runner, chat_session_id="sess-9",
    )

    out = await skill.execute("u1", {"tasks": [
        {"type": "explore", "task": "check whatsapp"},
        {"type": "explore", "task": "check email"},
    ]})

    # A group id was generated and the fan-out registered with it.
    assert captured["fanout_group_id"] is not None
    fake_runner.register_subagent_fanout.assert_called_once()
    reg_args = fake_runner.register_subagent_fanout.call_args
    assert reg_args.args[0] == captured["fanout_group_id"]
    assert reg_args.args[1] == "u1"
    assert reg_args.args[2] == ["subagent-0", "subagent-1"]
    # The skill tells the brain results will be consolidated automatically.
    assert "consolidated" in out.lower()


@pytest.mark.asyncio
async def test_skill_legacy_path_when_no_lane_queue(monkeypatch):
    from lazyclaw.skills.builtin import dispatch as dispatch_mod
    from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill

    captured = {}

    class _FakeDispatcher:
        def __init__(self, **kwargs):
            pass

        async def submit_async(self, configs, user_id, on_register=None,
                               on_settle=None, fanout_group_id=None):
            captured["fanout_group_id"] = fanout_group_id
            return [f"subagent-{i}" for i in range(len(configs))]

    monkeypatch.setattr(dispatch_mod, "AgentDispatcher", _FakeDispatcher)

    # No task_runner → legacy fire-and-forget, no consolidation group.
    skill = DispatchSubagentsSkill(
        config=MagicMock(), registry=MagicMock(), eco_router=MagicMock(),
        permission_checker=None, callback=MagicMock(), team_lead=None,
        task_runner=None,
    )
    out = await skill.execute("u1", {"tasks": [
        {"type": "explore", "task": "a"},
        {"type": "explore", "task": "b"},
    ]})
    assert captured["fanout_group_id"] is None
    assert "DO NOT wait" in out
