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
