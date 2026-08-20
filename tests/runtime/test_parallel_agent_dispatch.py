"""2026-08-20 sequential-fan-out incident — N sync `agent` calls in ONE
assistant message must actually run concurrently.

Prod evidence (11:00:31): the brain emitted 3 `agent` tool calls in one
message ("TOOL STATE: tool_calls_received=3, names=['agent','agent',
'agent']"), but team_lead registered specialist #2 only when #1 completed
at 11:03:18 — strict 3× sequential wall clock.

Root cause: the TAOR loop's ONLY concurrency path batches calls whose
skill has ``read_only=True`` (agent.py "Parallel pre-execution" block and
``ToolExecutor.execute_batch``). ``AgentDispatchSkill`` never set it (and
must not — dispatch is not a read), so every fan-out fell into the plain
sequential ``await`` loop. The agent_tool docstring's parallel promise
described the OLD dispatcher's internal gather, retired 2026-07-07.

Fix: a ``parallel_safe`` property (default = ``read_only``) as the
batching predicate; the dispatch skill opts in. Real throttling stays
with the loop semaphore (6) and domain locks (browser turn lock).
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.runtime import agent as agent_mod
from lazyclaw.runtime.tool_executor import ToolExecutor
from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.agent_tool import AgentDispatchSkill

_AGENT_SRC = inspect.getsource(agent_mod)


# ── predicate semantics ──────────────────────────────────────────────


class _PlainSkill(BaseSkill):
    """Defaults only — no read_only / parallel_safe overrides."""

    name = "plain"
    description = "d"
    parameters_schema: dict = {"type": "object", "properties": {}}

    async def execute(self, user_id, params):  # pragma: no cover
        return "ok"


class _ReadOnlySkill(_PlainSkill):
    name = "reader"
    read_only = True


def test_parallel_safe_defaults_to_read_only() -> None:
    assert _PlainSkill().parallel_safe is False
    assert _ReadOnlySkill().parallel_safe is True


def test_agent_dispatch_is_parallel_safe_but_not_read_only() -> None:
    """Dispatch may fan out (that IS its design), but it is not a read —
    `read_only` also feeds permission reasoning and must stay False."""
    skill = AgentDispatchSkill(
        config=None, registry=object(), eco_router=object(),
    )
    assert skill.parallel_safe is True
    assert skill.read_only is False


# ── executor batching honors parallel_safe ───────────────────────────


class _SleepingParallelSkill(BaseSkill):
    """parallel_safe worker that records overlap."""

    name = "agentish"
    description = "d"
    parameters_schema: dict = {"type": "object", "properties": {}}
    parallel_safe = True

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def execute(self, user_id, params):
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        return "done"


class _SleepingStateSkill(_SleepingParallelSkill):
    name = "stateful"
    parallel_safe = False

    def __init__(self) -> None:
        super().__init__()
        self.order: list[str] = []

    async def execute(self, user_id, params):
        self.order.append(params.get("n", "?"))
        return await super().execute(user_id, params)


class _Registry:
    def __init__(self, skills: dict[str, BaseSkill]) -> None:
        self._skills = skills

    def get(self, name):
        return self._skills.get(name)

    def get_display_name(self, name):
        return name


@pytest.mark.asyncio
async def test_execute_batch_gathers_parallel_safe_calls() -> None:
    skill = _SleepingParallelSkill()
    executor = ToolExecutor(registry=_Registry({"agentish": skill}))
    calls = [
        ToolCall(id=f"c{i}", name="agentish", arguments={}) for i in range(3)
    ]

    outcomes = await executor.execute_batch(calls, "u1")

    assert skill.peak > 1, (
        "parallel_safe calls must overlap — sequential execution is the "
        "2026-08-20 3x-wall-clock incident"
    )
    assert [tc.id for tc, _r, _d, _g in outcomes] == ["c0", "c1", "c2"]


@pytest.mark.asyncio
async def test_execute_batch_keeps_state_tools_sequential_in_order() -> None:
    skill = _SleepingStateSkill()
    executor = ToolExecutor(registry=_Registry({"stateful": skill}))
    calls = [
        ToolCall(id=f"s{i}", name="stateful", arguments={"n": str(i)})
        for i in range(3)
    ]

    await executor.execute_batch(calls, "u1")

    assert skill.peak == 1
    assert skill.order == ["0", "1", "2"]


# ── source wiring: the TAOR loop batches on parallel_safe and
#    announces batched tool_calls at spawn time ──────────────────────


def test_taor_batch_predicate_is_parallel_safe() -> None:
    """The pre-execution block must batch on `parallel_safe`, not
    `read_only` — otherwise agent dispatches keep serializing."""
    idx = _AGENT_SRC.index("Parallel pre-execution")
    window = _AGENT_SRC[idx:idx + 1800]
    assert 'getattr(_skill, "parallel_safe", False)' in window
    assert 'getattr(_skill, "read_only", False)' not in window


def test_executor_batch_predicate_is_parallel_safe() -> None:
    import lazyclaw.runtime.tool_executor as te

    src = inspect.getsource(te.ToolExecutor.execute_batch)
    assert 'getattr(skill, "parallel_safe", False)' in src
    assert 'getattr(skill, "read_only", False)' not in src


def test_batched_tool_calls_announced_before_gather() -> None:
    """Batched calls run BEFORE the sequential loop reaches them — the
    UI's `tool_call` frame must be emitted at spawn (pre-batch), else a
    3-minute parallel dispatch shows NO chips while running and then
    mounts-and-settles them instantly at the end. The loop-top emit must
    skip already-announced ids or the append-only mobile reducer renders
    duplicate chips."""
    idx = _AGENT_SRC.index("Parallel pre-execution")
    window = _AGENT_SRC[idx:idx + 3000]
    assert "_announced_tool_ids" in window, (
        "batch block must announce tool_call events at spawn"
    )
    loop_idx = _AGENT_SRC.index("for tc in _tool_calls_to_run:")
    loop_window = _AGENT_SRC[loop_idx:loop_idx + 1600]
    assert "tc.id not in _announced_tool_ids" in loop_window, (
        "loop-top tool_call emit must not double-announce batched calls"
    )
