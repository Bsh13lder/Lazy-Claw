"""ClaudeSDKProvider real streaming under MODE_CLAUDE.

The legacy ``stream_chat`` implementation awaited the entire ``chat()``
response and yielded ONE chunk with ``done=True`` — Web UI saw no
incremental output even though the underlying ``sdk_query`` async
generator emits AssistantMessage blocks as they arrive. Under
MODE_CLAUDE a 60–120s brain turn looked frozen: "typing…" never
resolved, no partial text, no thinking indicator.

These tests pin real streaming: ``stream_chat`` MUST yield a chunk
per AssistantMessage TextBlock as the SDK emits it, with at most one
final ``done=True`` chunk at the end.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch

import pytest

from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.llm.providers.claude_sdk_provider import ClaudeSDKProvider


def _fake_streaming_sdk_query(chunks: list[tuple[str, float]]):
    """Build a fake ``sdk_query`` that yields multiple text blocks with
    inter-chunk delays. ``chunks`` is [(text, delay_after_s), …].
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
    )

    async def fake_query(*, prompt: str, options: Any):  # noqa: ARG001
        for text, delay in chunks:
            yield AssistantMessage(
                content=[TextBlock(text=text)],
                model="claude-sonnet",
            )
            if delay > 0:
                await asyncio.sleep(delay)
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=10,
            is_error=False,
            num_turns=1,
            session_id="test-session",
            total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="",
        )

    return fake_query


@pytest.mark.skipif(
    not ClaudeSDKProvider(model="sonnet")._claude_bin,
    reason="claude binary not on PATH — SDK provider untestable",
)
async def test_stream_chat_yields_chunks_incrementally_not_buffered() -> None:
    """Each AssistantMessage TextBlock from sdk_query must surface as
    its own StreamChunk in real time. The legacy implementation
    collapsed everything to ONE final done=True chunk.
    """
    provider = ClaudeSDKProvider(model="sonnet")
    chunks_spec = [
        ("Hello ", 0.10),
        ("from ", 0.10),
        ("Claude.", 0.0),
    ]
    fake = _fake_streaming_sdk_query(chunks_spec)

    arrival_times: list[float] = []
    deltas: list[str] = []
    dones: list[bool] = []
    started = time.monotonic()

    with patch("claude_agent_sdk.query", new=fake):
        async for chunk in provider.stream_chat(
            [LLMMessage(role="user", content="hi")],
        ):
            arrival_times.append(time.monotonic() - started)
            deltas.append(chunk.delta)
            dones.append(chunk.done)

    # Concatenated text must be the full response.
    assert "".join(deltas) == "Hello from Claude.", (
        f"Concatenated stream != expected; got deltas={deltas!r}"
    )
    # Exactly one done=True chunk, and it must be the last.
    assert dones[-1] is True, f"Last chunk must have done=True; got dones={dones}"
    assert sum(1 for d in dones if d) == 1, (
        f"Expected exactly one done=True chunk; got {sum(1 for d in dones if d)}"
    )
    # Multiple delta-bearing chunks before the final one — proves streaming.
    deltas_before_done = [d for d, done in zip(deltas, dones) if not done]
    assert len(deltas_before_done) >= 2, (
        f"Expected ≥2 delta chunks before terminal done=True (real streaming); "
        f"got {len(deltas_before_done)}. deltas={deltas!r} dones={dones!r}"
    )
    # First chunk must arrive BEFORE the second yield's 100ms delay.
    # Legacy implementation awaits the whole thing → first chunk
    # lands at ~0.20s. Real streaming → first chunk lands at <0.05s.
    assert arrival_times[0] < 0.08, (
        f"First chunk arrived at {arrival_times[0]:.3f}s — looks buffered "
        f"(real streaming should be <0.08s). arrival_times={arrival_times}"
    )


@pytest.mark.skipif(
    not ClaudeSDKProvider(model="sonnet")._claude_bin,
    reason="claude binary not on PATH — SDK provider untestable",
)
async def test_stream_chat_emits_tool_calls_in_terminal_chunk() -> None:
    """Tool calls accumulated during the SDK stream must surface in the
    final done=True chunk so taor.py's streaming caller can dispatch
    them on the next turn (same contract as the non-streaming chat()).
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        ToolUseBlock,
    )

    async def fake(*, prompt: str, options: Any):  # noqa: ARG001
        yield AssistantMessage(
            content=[ToolUseBlock(id="t1", name="mcp__lazyclaw__noop", input={})],
            model="claude-sonnet",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=5, duration_api_ms=5,
            is_error=False, num_turns=1, session_id="s",
            total_cost_usd=0.0, usage={"input_tokens": 1, "output_tokens": 1},
            result="",
        )

    provider = ClaudeSDKProvider(model="sonnet")
    final_chunk = None
    with patch("claude_agent_sdk.query", new=fake):
        async for chunk in provider.stream_chat(
            [LLMMessage(role="user", content="x")],
            tools=[{
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "noop",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        ):
            if chunk.done:
                final_chunk = chunk

    assert final_chunk is not None, "Expected a terminal done=True chunk"
    assert final_chunk.tool_calls and len(final_chunk.tool_calls) == 1
    assert final_chunk.tool_calls[0].name == "noop"
