"""Unified `agent` dispatch skill — sync + background paths."""
import asyncio

import pytest

from lazyclaw.runtime.dispatcher import _IS_SUBAGENT
from lazyclaw.skills.builtin.agent_tool import (
    MAX_AGENT_RESULT_CHARS,
    MAX_AGENTS_PER_TURN,
    AgentDispatchSkill,
    clip_agent_result,
)
from lazyclaw.teams.runner import SpecialistResult


def _make_skill(**overrides):
    kwargs = dict(
        config=None, registry=object(), eco_router=object(),
        permission_checker=None, callback=None, team_lead=None,
        task_runner=None, chat_session_id="sess-1", fanout_group_id="fg-1",
    )
    kwargs.update(overrides)
    return AgentDispatchSkill(**kwargs)


def _ok_result(text="all done"):
    return SpecialistResult(
        agent_name="explore", task="t", result=text,
        tools_used=("web_search",), model_used="worker", duration_ms=10,
    )


@pytest.fixture
def fake_run_specialist(monkeypatch):
    calls = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return _ok_result()

    monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", _fake)
    return calls


def test_schema_shape():
    skill = _make_skill()
    schema = skill.parameters_schema
    props = schema["properties"]
    assert "explore" in props["agent_type"]["enum"]
    assert "general_purpose" in props["agent_type"]["enum"]
    assert "browser" in props["agent_type"]["enum"]
    assert props["run_in_background"]["default"] is False
    assert schema["required"] == ["agent_type", "task"]
    assert skill.name == "agent"


def test_sync_returns_result_in_turn(fake_run_specialist):
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "find X",
    }))
    assert "all done" in out
    assert "[agent:explore]" in out
    assert len(fake_run_specialist) == 1
    assert fake_run_specialist[0]["task"] == "find X"


def test_unknown_agent_type_lists_valid(fake_run_specialist):
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "wat", "task": "x",
    }))
    assert "Unknown agent_type" in out
    assert "explore" in out
    assert not fake_run_specialist


def test_missing_task_errors(fake_run_specialist):
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {"agent_type": "explore"}))
    assert out.startswith("Error")
    assert not fake_run_specialist


def test_depth_guard_blocks_nested(fake_run_specialist):
    skill = _make_skill()

    async def _run():
        token = _IS_SUBAGENT.set(True)
        try:
            return await skill.execute("u1", {
                "agent_type": "explore", "task": "x",
            })
        finally:
            _IS_SUBAGENT.reset(token)

    out = asyncio.run(_run())
    assert "single-depth" in out
    assert not fake_run_specialist


def test_per_turn_cap(fake_run_specialist):
    skill = _make_skill()
    skill._calls_this_turn = MAX_AGENTS_PER_TURN
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x",
    }))
    assert "fan-out cap" in out
    assert not fake_run_specialist


def test_timeout_returns_timeout_status(monkeypatch):
    import lazyclaw.skills.builtin.agent_tool as at
    monkeypatch.setattr(at, "_MIN_TIMEOUT_S", 0)

    async def _slow(**kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", _slow)
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x", "timeout": 0,
    }))
    assert "TIMEOUT" in out


def test_crash_returns_failed_status(monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("kaput")

    monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", _boom)
    skill = _make_skill()
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x",
    }))
    assert "FAILED" in out
    assert "kaput" in out


def test_clip_appends_marker():
    text = "x" * (MAX_AGENT_RESULT_CHARS + 500)
    out = clip_agent_result(text)
    assert len(out) < len(text)
    assert "[truncated 500 chars]" in out


def test_clip_noop_under_cap():
    assert clip_agent_result("short") == "short"


def test_sync_marks_subagent_context(monkeypatch):
    """run_specialist executes with _IS_SUBAGENT=True; caller context stays False."""
    seen = {}

    async def _probe(**kwargs):
        seen["flag"] = _IS_SUBAGENT.get()
        return _ok_result()

    monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", _probe)
    skill = _make_skill()

    async def _run():
        out = await skill.execute("u1", {"agent_type": "explore", "task": "x"})
        return out, _IS_SUBAGENT.get()

    out, after = asyncio.run(_run())
    assert seen["flag"] is True
    assert after is False
    assert "all done" in out


def test_concurrency_semaphore(monkeypatch):
    """6 concurrent max by default; excess queue."""
    import lazyclaw.skills.builtin.agent_tool as at
    at._SEMAPHORES.clear()
    monkeypatch.setenv("LAZYCLAW_DISPATCH_CONCURRENCY", "2")

    active = {"now": 0, "peak": 0}

    async def _tracked(**kwargs):
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        await asyncio.sleep(0.05)
        active["now"] -= 1
        return _ok_result()

    monkeypatch.setattr("lazyclaw.teams.runner.run_specialist", _tracked)
    skill = _make_skill()

    async def _fan():
        return await asyncio.gather(*[
            skill.execute("u1", {"agent_type": "explore", "task": f"t{i}"})
            for i in range(5)
        ])

    results = asyncio.run(_fan())
    at._SEMAPHORES.clear()
    assert len(results) == 5
    assert active["peak"] <= 2


class FakeTaskRunner:
    def __init__(self, raise_exc: Exception | None = None):
        self.submits = []
        self._raise = raise_exc

    async def submit(self, **kwargs):
        if self._raise:
            raise self._raise
        self.submits.append(kwargs)
        return "task123456789"


def test_background_routes_to_task_runner(fake_run_specialist):
    tr = FakeTaskRunner()
    skill = _make_skill(task_runner=tr)
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "long scrape",
        "run_in_background": True,
    }))
    assert "Background agent 'explore' started" in out
    assert not fake_run_specialist          # sync path NOT taken
    sub = tr.submits[0]
    assert sub["instruction"] == "long scrape"
    assert sub["source"] == "brain"
    assert sub["fanout_group_id"] == "fg-1"
    assert sub["chat_session_id"] == "sess-1"
    assert sub["name"] == "agent:explore"


def test_background_without_runner_errors():
    skill = _make_skill(task_runner=None)
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x", "run_in_background": True,
    }))
    assert "not configured" in out


def test_background_submit_rejection_surfaces():
    tr = FakeTaskRunner(raise_exc=RuntimeError("per-user cap reached"))
    skill = _make_skill(task_runner=tr)
    out = asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x", "run_in_background": True,
    }))
    assert "Cannot start background agent" in out
    assert "per-user cap reached" in out


def test_background_does_not_consume_sync_cap():
    tr = FakeTaskRunner()
    skill = _make_skill(task_runner=tr)
    before = skill._calls_this_turn
    asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "x", "run_in_background": True,
    }))
    assert skill._calls_this_turn == before


def test_agent_in_brain_gating_sets():
    from lazyclaw.runtime import agent as agent_mod
    assert "agent" in agent_mod._BASE_TOOL_NAMES
    assert "agent" in agent_mod._META_TOOLS
    assert "agent" in agent_mod._DISPATCH_ONLY_TOOLS
    assert "agent" in agent_mod._LOCAL_TOOL_NAMES


def test_agent_result_cap_constant_matches_skill():
    from lazyclaw.runtime import agent as agent_mod
    assert agent_mod._MAX_TOOL_RESULT_CHARS_AGENT == MAX_AGENT_RESULT_CHARS
    assert agent_mod._MAX_TOOL_RESULT_CHARS_AGENT > agent_mod._MAX_TOOL_RESULT_CHARS


def test_all_already_dispatched_gates_include_agent():
    """Every 'brain already dispatched' failsafe in agent.py must treat
    the `agent` tool as a real dispatch — a missed site = double-dispatch
    (2026-06-08 triple-execution incident class)."""
    import pathlib

    import lazyclaw.runtime.agent as agent_mod

    src = pathlib.Path(agent_mod.__file__).read_text(encoding="utf-8")
    # Shape 1: inline `"dispatch_subagents" not in _called_tool_names` chains
    # must each have an adjacent agent line.
    assert src.count('"dispatch_subagents" not in _called_tool_names') == \
        src.count('"agent" not in _called_tool_names'), (
        "an inline already-dispatched chain is missing the agent skip"
    )
    # Shape 2: tuple-driven any() gates must include "agent".
    assert 'for d in ("delegate", "dispatch_subagents", "run_background")' not in src, (
        "a tuple-shaped already-dispatched gate is missing \"agent\""
    )


class _FakeTeamLead:
    """Captures register() kwargs so the cancel-token wiring is pinned."""

    def __init__(self):
        self.registered = []

    def register(self, **kwargs):
        self.registered.append(kwargs)


def test_sync_dispatch_registers_live_cancel_token(fake_run_specialist):
    """The user's stop button fires team_lead.request_cancel → token.cancel().

    Incident 2026-08-16 10:46: sync specialist dispatches registered NO
    cancel token ("request_cancel denied reason=no_token"), so the user's
    cancel was silently ignored and an unwanted browser task ground on to
    its 600s budget. The same token object must reach BOTH team_lead
    (so request_cancel can fire it) and run_specialist (whose loop checks
    is_cancelled every iteration).
    """
    lead = _FakeTeamLead()
    skill = _make_skill(team_lead=lead)
    asyncio.run(skill.execute("u1", {
        "agent_type": "explore", "task": "find X",
    }))

    assert len(lead.registered) == 1
    registered_token = lead.registered[0].get("cancel_token")
    assert registered_token is not None, (
        "sync dispatch must register a cancel token or the stop button "
        "is a no-op (no_token denial)"
    )
    passed_token = fake_run_specialist[0].get("cancel_token")
    assert passed_token is registered_token, (
        "team_lead and run_specialist must share the SAME token object"
    )
    # Firing it must flip the flag the runner polls.
    registered_token.cancel()
    assert registered_token.is_cancelled is True
