"""Dispatcher: concurrency cap + team_lead bookkeeping + non-blocking submit.

The dispatcher must:
1. Cap concurrent subagent runs in the legacy ``dispatch()`` path so we don't
   trip per-account 429s on parallel LLM calls.
2. Register every subagent with TeamLead under ``lane='subagent'`` so the
   Activity panel shows the work in flight.
3. Expose ``submit_async`` that returns immediately with task IDs and
   publishes terminal events to ``task_event_bus`` — the path the
   ``dispatch_subagents`` skill uses today, which keeps the foreground chat
   responsive.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from lazyclaw.runtime import task_event_bus
from lazyclaw.runtime.dispatcher import (
    AgentDispatcher,
    AgentType,
    SubagentConfig,
    SubagentResult,
    _dispatch_concurrency,
)


# ── Concurrency cap ───────────────────────────────────────────────────


def test_default_cap_is_four(monkeypatch):
    monkeypatch.delenv("LAZYCLAW_DISPATCH_CONCURRENCY", raising=False)
    assert _dispatch_concurrency() == 4


def test_env_override(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_DISPATCH_CONCURRENCY", "7")
    assert _dispatch_concurrency() == 7


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_DISPATCH_CONCURRENCY", "not-a-number")
    assert _dispatch_concurrency() == 4


@pytest.mark.asyncio
async def test_dispatch_caps_concurrent_runs(monkeypatch):
    monkeypatch.setenv("LAZYCLAW_DISPATCH_CONCURRENCY", "3")

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def fake_run_subagent(self, cfg, user_id, task_id_override=None):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return SubagentResult(
            agent_type=cfg.agent_type, task=cfg.task, result="ok",
            success=True, tokens_used=0, duration_ms=50,
        )

    monkeypatch.setattr(AgentDispatcher, "_run_subagent", fake_run_subagent)

    dispatcher = AgentDispatcher.__new__(AgentDispatcher)
    configs = [
        SubagentConfig(
            agent_type=AgentType.EXPLORE,
            task=f"task-{i}",
            tool_names=("web_search",),
        )
        for i in range(12)
    ]
    results = await dispatcher.dispatch(configs, user_id="u1")
    assert len(results) == 12
    assert all(r.success for r in results)
    assert peak <= 3, f"concurrency cap exceeded: peak={peak}"


# ── TeamLead bookkeeping ──────────────────────────────────────────────


def _build_dispatcher_with_team_lead(team_lead):
    """Build a real AgentDispatcher with mocked deps, real team_lead."""
    return AgentDispatcher(
        config=MagicMock(),
        eco_router=MagicMock(),
        registry=MagicMock(),
        permission_checker=None,
        team_lead=team_lead,
        callback=None,
    )


@pytest.mark.asyncio
async def test_dispatch_registers_each_subagent_with_team_lead(monkeypatch):
    """Each subagent must register on entry and complete on success."""
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    async def fake_run_specialist(**kwargs):
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result="ok",
            tools_used=(),
            model_used="test",
            duration_ms=10,
            success=True,
            error=None,
        )

    monkeypatch.setattr(teams_runner, "run_specialist", fake_run_specialist)

    team_lead = MagicMock()
    dispatcher = _build_dispatcher_with_team_lead(team_lead)

    configs = [
        SubagentConfig(agent_type=AgentType.EXPLORE, task=f"t-{i}")
        for i in range(3)
    ]
    results = await dispatcher.dispatch(configs, user_id="u1")
    assert len(results) == 3
    assert all(r.success for r in results)

    assert team_lead.register.call_count == 3
    for call in team_lead.register.call_args_list:
        kwargs = call.kwargs
        assert kwargs["lane"] == "subagent"
        assert kwargs["user_id"] == "u1"
        assert kwargs["task_id"].startswith("subagent-")
        assert kwargs["instruction_full"].startswith("t-")

    assert team_lead.complete.call_count == 3
    assert team_lead.fail.call_count == 0


@pytest.mark.asyncio
async def test_dispatch_marks_failed_subagents(monkeypatch):
    """When run_specialist returns success=False, team_lead.fail must be
    called instead of complete."""
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    async def fake_run_specialist(**kwargs):
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result="",
            tools_used=(),
            model_used="test",
            duration_ms=5,
            success=False,
            error="kaboom",
        )

    monkeypatch.setattr(teams_runner, "run_specialist", fake_run_specialist)

    team_lead = MagicMock()
    dispatcher = _build_dispatcher_with_team_lead(team_lead)

    configs = [SubagentConfig(agent_type=AgentType.EXPLORE, task="t")]
    results = await dispatcher.dispatch(configs, user_id="u1")
    assert results[0].success is False

    team_lead.fail.assert_called_once()
    assert team_lead.fail.call_args.kwargs["error"] == "kaboom"
    team_lead.complete.assert_not_called()


# ── submit_async (non-blocking) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_async_returns_immediately(monkeypatch):
    """submit_async must return task IDs without waiting for the run.

    Even if every subagent sleeps 0.5s, submit_async should complete in
    well under that — the work happens on background tasks.
    """
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    async def slow_run_specialist(**kwargs):
        await asyncio.sleep(0.5)
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result="ok",
            tools_used=(),
            model_used="test",
            duration_ms=500,
            success=True,
            error=None,
        )

    monkeypatch.setattr(teams_runner, "run_specialist", slow_run_specialist)

    team_lead = MagicMock()
    dispatcher = _build_dispatcher_with_team_lead(team_lead)
    configs = [
        SubagentConfig(agent_type=AgentType.EXPLORE, task=f"t-{i}")
        for i in range(3)
    ]

    start = time.monotonic()
    task_ids = await dispatcher.submit_async(configs, user_id="u1")
    elapsed_ms = (time.monotonic() - start) * 1000

    assert len(task_ids) == 3
    assert all(tid.startswith("subagent-") for tid in task_ids)
    assert elapsed_ms < 100, (
        f"submit_async should be near-instant, took {elapsed_ms:.0f}ms"
    )


@pytest.mark.asyncio
async def test_submit_async_publishes_terminal_events(monkeypatch):
    """Each subagent must publish a background_done / background_failed
    event to task_event_bus when it finishes."""
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    call_count = {"n": 0}

    async def fake_run_specialist(**kwargs):
        call_count["n"] += 1
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result=f"r{call_count['n']}",
            tools_used=(),
            model_used="test",
            duration_ms=1,
            success=True,
            error=None,
        )

    monkeypatch.setattr(teams_runner, "run_specialist", fake_run_specialist)

    team_lead = MagicMock()
    dispatcher = _build_dispatcher_with_team_lead(team_lead)

    user_id = "u_pub_test"
    task_event_bus.clear_user(user_id)

    received: list = []

    async def consume():
        async for evt in task_event_bus.subscribe(user_id):
            received.append(evt)
            if len(received) >= 2:
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let subscriber register

    configs = [
        SubagentConfig(agent_type=AgentType.EXPLORE, task="a"),
        SubagentConfig(agent_type=AgentType.EXPLORE, task="b"),
    ]
    task_ids = await dispatcher.submit_async(configs, user_id=user_id)

    await asyncio.wait_for(consumer, timeout=2.0)

    assert len(received) == 2
    assert {e.kind for e in received} == {"background_done"}
    assert {e.task_id for e in received} == set(task_ids)


@pytest.mark.asyncio
async def test_submit_async_uses_subagent_lane(monkeypatch):
    """team_lead.register must be called with lane='subagent' so the
    frontend filter (Activity panel) can group these correctly."""
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    async def fake_run_specialist(**kwargs):
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result="ok",
            tools_used=(),
            model_used="test",
            duration_ms=1,
            success=True,
            error=None,
        )

    monkeypatch.setattr(teams_runner, "run_specialist", fake_run_specialist)

    team_lead = MagicMock()
    dispatcher = _build_dispatcher_with_team_lead(team_lead)
    configs = [SubagentConfig(agent_type=AgentType.EXPLORE, task="t")]
    task_ids = await dispatcher.submit_async(configs, user_id="u1")

    # Wait for the background task to complete its register call
    for _ in range(50):
        if team_lead.register.called:
            break
        await asyncio.sleep(0.02)

    assert team_lead.register.called
    assert team_lead.register.call_args.kwargs["lane"] == "subagent"
    assert team_lead.register.call_args.kwargs["task_id"] == task_ids[0]


# ── Skill-level contract ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_skill_returns_handles_not_results(monkeypatch):
    """The skill must return task IDs immediately — not wait for results.

    Pins the brain-facing contract: the tool result starts with 'Dispatched'
    and includes 'Task IDs:'.
    """
    from lazyclaw.skills.builtin.dispatch import DispatchSubagentsSkill
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    async def slow_run_specialist(**kwargs):
        await asyncio.sleep(0.5)
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result="ok",
            tools_used=(),
            model_used="test",
            duration_ms=500,
            success=True,
            error=None,
        )

    monkeypatch.setattr(teams_runner, "run_specialist", slow_run_specialist)

    skill = DispatchSubagentsSkill(
        config=MagicMock(),
        registry=MagicMock(),
        eco_router=MagicMock(),
        permission_checker=None,
        team_lead=MagicMock(),
    )

    start = time.monotonic()
    out = await skill.execute("u1", {
        "tasks": [
            {"type": "explore", "task": "research X"},
            {"type": "explore", "task": "research Y"},
        ],
    })
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 100, f"skill blocked for {elapsed_ms:.0f}ms"
    assert out.startswith("Dispatched 2 subagents")
    assert "Task IDs:" in out
    assert "subagent-" in out


# ── Subagent silence (no chat-WS leakage) ─────────────────────────────


@pytest.mark.asyncio
async def test_subagent_callback_does_not_leak_to_brain(monkeypatch):
    """Background subagents must be silent on the brain's callback.

    Pins the architectural rule: a parallel subagent's per-step events
    (specialist_thinking / specialist_tool / phase / token / tool_call /
    tool_result) must NOT reach the foreground brain callback. They are
    Activity-panel-only via TeamLead → task_event_bus.

    Regression guard: dispatcher used to wrap the brain's chat-WS
    callback in StepTrackingCallback, which forwarded every event to it
    and dumped raw subagent traffic into the user's chat window.
    """
    from lazyclaw.runtime.callbacks import AgentEvent
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    brain_events: list[str] = []

    class RecordingBrainCallback:
        async def on_event(self, event: AgentEvent) -> None:
            brain_events.append(event.kind)

        async def on_approval_request(self, skill_name, arguments):
            return False

        async def on_help_request(self, context, needs_browser):
            return "skip"

    # Simulate a subagent that emits the full event spectrum a real
    # specialist would: specialist_thinking, specialist_tool, phase,
    # token, tool_call, tool_result.
    async def chatty_run_specialist(**kwargs):
        cb = kwargs.get("callback")
        if cb:
            for kind in (
                "specialist_thinking", "specialist_tool", "phase",
                "token", "tool_call", "tool_result", "thinking_delta",
            ):
                await cb.on_event(AgentEvent(kind=kind, detail="x", metadata={}))
        return SpecialistResult(
            agent_name="explore_agent",
            task=kwargs.get("task", ""),
            result="done",
            tools_used=("web_search",),
            model_used="test",
            duration_ms=1,
            success=True,
            error=None,
        )

    monkeypatch.setattr(teams_runner, "run_specialist", chatty_run_specialist)

    team_lead = MagicMock()
    brain_cb = RecordingBrainCallback()

    dispatcher = AgentDispatcher(
        config=MagicMock(),
        eco_router=MagicMock(),
        registry=MagicMock(),
        permission_checker=None,
        team_lead=team_lead,
        callback=brain_cb,  # the brain's foreground chat-WS callback
    )

    configs = [SubagentConfig(agent_type=AgentType.EXPLORE, task="t")]
    results = await dispatcher.dispatch(configs, user_id="u_silent")

    assert results[0].success
    assert brain_events == [], (
        f"subagent leaked {brain_events!r} into brain/chat callback"
    )


@pytest.mark.asyncio
async def test_subagent_specialist_tool_still_drives_team_lead(monkeypatch):
    """Silent callback must still surface live tool progress in the
    Activity panel by calling team_lead.update_step on `specialist_tool`
    events. Otherwise the panel would freeze on 'queued'.
    """
    from lazyclaw.runtime.callbacks import AgentEvent
    from lazyclaw.teams.runner import SpecialistResult
    from lazyclaw.teams import runner as teams_runner

    async def chatty_run_specialist(**kwargs):
        cb = kwargs.get("callback")
        if cb:
            await cb.on_event(AgentEvent(
                kind="specialist_tool", detail="web_search",
                metadata={"specialist": "explore_agent", "tool": "web_search"},
            ))
            await cb.on_event(AgentEvent(
                kind="specialist_tool", detail="read_file",
                metadata={"specialist": "explore_agent", "tool": "read_file"},
            ))
        return SpecialistResult(
            agent_name="explore_agent",
            task="t", result="ok", tools_used=("web_search", "read_file"),
            model_used="test", duration_ms=1, success=True, error=None,
        )

    monkeypatch.setattr(teams_runner, "run_specialist", chatty_run_specialist)

    team_lead = MagicMock()
    dispatcher = _build_dispatcher_with_team_lead(team_lead)

    configs = [SubagentConfig(agent_type=AgentType.EXPLORE, task="t")]
    await dispatcher.dispatch(configs, user_id="u_step")

    steps = [c.args for c in team_lead.update_step.call_args_list]
    assert len(steps) == 2
    assert steps[0][1] == "web_search"
    assert steps[1][1] == "read_file"
