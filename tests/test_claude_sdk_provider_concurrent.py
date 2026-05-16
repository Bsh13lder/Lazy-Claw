"""ClaudeSDKProvider concurrent-call safety.

The provider mutates ``self._tool_name_map`` on every ``chat()`` call
(was written in ``_build_options``, read in ``_unmap_tool_name`` during
the ``sdk_query`` async-for loop). When two ``chat()`` calls overlap on
the same provider instance — which is the normal case in lazyclaw,
since EcoRouter caches one provider per process and all background
sub-agents share it — the second call's ``_build_options`` clobbers the
first call's map, and any ``ToolUseBlock`` arriving after the clobber
resolves against the wrong map.

Symptoms observed in production: tool names appearing "short" (no
registry-UUID prefix) because the swap dropped them out of the map
entirely; the agent then drops the tool_use as hallucinated (see
claude_sdk_provider.py:550-571).

These tests pin the fix: ``_tool_name_map`` must be LOCAL to each
``chat()`` invocation, not shared state on ``self``. The provider is
allowed to keep ``self._tool_name_map`` for legacy / introspection but
must not rely on it for correctness.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import patch

import pytest

from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.llm.providers.claude_sdk_provider import ClaudeSDKProvider


# ── Helpers ───────────────────────────────────────────────────────────


def _tool_spec(short: str, full_name: str) -> dict:
    """A neutral OpenAI-format tool dict matching what eco_router emits."""
    return {
        "type": "function",
        "function": {
            "name": full_name,
            "description": f"test tool {short}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _make_dispatch_fake_query(delay_s: float = 0.0):
    """Build ONE fake ``claude_agent_sdk.query`` that dispatches by which
    tool is in ``options.allowed_tools`` — concurrent tasks share this
    single fake (module-global patch can't be call-scoped) and each call
    yields the ToolUseBlock matching its OWN allowed_tools.

    ``delay_s`` is inserted between options-inspection and the tool_use
    yield so call B can run ``_build_options`` while call A is parked at
    the ``await`` — exactly the race window we're testing.
    """
    from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock

    async def fake_query(*, prompt: str, options: Any):  # noqa: ARG001
        # Pick the first allowed_tools entry that's an mcp__lazyclaw__ tool.
        allowed = getattr(options, "allowed_tools", None) or []
        sdk_name = next(
            (t for t in allowed if t.startswith("mcp__lazyclaw__")),
            "mcp__lazyclaw__unknown",
        )
        await asyncio.sleep(delay_s)
        yield AssistantMessage(
            content=[ToolUseBlock(id="tu-1", name=sdk_name, input={})],
            model="claude-sonnet",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=10,
            is_error=False,
            num_turns=1,
            session_id="test-session",
            total_cost_usd=0.0,
            usage={
                "input_tokens": 1,
                "output_tokens": 1,
            },
            result="",
        )

    return fake_query


# ── Tests ─────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not ClaudeSDKProvider(model="sonnet")._claude_bin,
    reason="claude binary not on PATH — SDK provider untestable",
)
async def test_concurrent_chat_calls_do_not_clobber_each_others_tool_maps() -> None:
    """Two overlapping ``chat()`` calls with DIFFERENT tool sets must each
    return their own tool's registry name, not swap.

    Before the fix: call B's ``_build_options`` overwrites
    ``self._tool_name_map`` with B's map between A's ``_build_options``
    and A's tool_use yield. A's resolver then can't find ``alpha_tool``
    in B's map → returns the unmapped short name → wrong result.
    """
    provider = ClaudeSDKProvider(model="sonnet")

    tools_a = [_tool_spec("alpha_tool", "mcp_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_alpha_tool")]
    tools_b = [_tool_spec("beta_tool", "mcp_11111111-2222-3333-4444-555555555555_beta_tool")]

    # One shared fake (50ms delay) so call B's _build_options runs
    # during call A's await — exact race window.
    fake = _make_dispatch_fake_query(delay_s=0.05)

    async def run_call(tools, label):
        return await provider.chat(
            [LLMMessage(role="user", content=f"hello {label}")],
            tools=tools,
        )

    with patch("claude_agent_sdk.query", new=fake):
        resp_a, resp_b = await asyncio.gather(
            run_call(tools_a, "a"),
            run_call(tools_b, "b"),
        )

    # Each call's ToolCall must resolve back to ITS OWN registry name.
    # If the maps clobbered, both will report the second-staged name
    # (or both will be the short un-prefixed form because the map went empty).
    assert len(resp_a.tool_calls) == 1, f"Call A: {resp_a.tool_calls}"
    assert len(resp_b.tool_calls) == 1, f"Call B: {resp_b.tool_calls}"
    assert resp_a.tool_calls[0].name == "mcp_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_alpha_tool", (
        f"Call A resolved to {resp_a.tool_calls[0].name!r} — "
        "concurrent call B clobbered A's _tool_name_map."
    )
    assert resp_b.tool_calls[0].name == "mcp_11111111-2222-3333-4444-555555555555_beta_tool", (
        f"Call B resolved to {resp_b.tool_calls[0].name!r}"
    )


@pytest.mark.skipif(
    not ClaudeSDKProvider(model="sonnet")._claude_bin,
    reason="claude binary not on PATH — SDK provider untestable",
)
async def test_concurrent_chat_calls_run_in_parallel_not_serialized() -> None:
    """The race fix must NOT introduce a global lock on chat(). Background
    sub-agents share the provider; serializing chat() would re-create the
    very foreground-blocking bug Fix #1 just eliminated.
    """
    import time as _t

    provider = ClaudeSDKProvider(model="sonnet")
    fake = _make_dispatch_fake_query(delay_s=0.20)
    tools = [_tool_spec("alpha_tool", "mcp_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee_alpha_tool")]

    async def run_call():
        return await provider.chat(
            [LLMMessage(role="user", content="x")], tools=tools,
        )

    started = _t.monotonic()
    with patch("claude_agent_sdk.query", new=fake):
        await asyncio.gather(run_call(), run_call(), run_call(), run_call())
    elapsed = _t.monotonic() - started

    # 4 calls × 0.20s each = 0.80s serialized; ~0.20s parallel + overhead.
    # Allow generous headroom; serialization would be ≥0.75s.
    assert elapsed < 0.60, (
        f"4 concurrent chat() calls took {elapsed:.2f}s — "
        "looks serialized (expected parallel <0.60s)."
    )
