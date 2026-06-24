"""AnthropicProvider.stream_chat terminal-event hygiene.

2026-06-24 audit (LOW): the MiniMax counter pump and the terminal
``StreamChunk(..., done=True)`` lived inside the ``message_delta`` branch.
The Anthropic streaming protocol normally emits exactly one ``message_delta``,
but a server (or a MiniMax compat adapter) that emits two would double-count
the diagnostics counters AND yield two terminal ``done=True`` chunks — which
downstream consumers treat as the end of the stream. This pins the once-only
guard: regardless of how many ``message_delta`` events arrive, exactly one
done chunk is yielded and the counters increment exactly once.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lazyclaw.llm.providers.anthropic_provider import AnthropicProvider
from lazyclaw.llm.providers.base import LLMMessage


class _FakeUsage:
    output_tokens = 7


def _message_delta_event() -> SimpleNamespace:
    """A minimal ``message_delta`` event carrying usage."""
    return SimpleNamespace(type="message_delta", usage=_FakeUsage())


def _text_delta_event(text: str = "narrating instead of dispatching") -> SimpleNamespace:
    """A ``content_block_delta`` text event so ``collected_text`` is non-empty
    (the MiniMax text-only counter requires streamed text)."""
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def _message_start_event() -> SimpleNamespace:
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=11,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )
        ),
    )


class _FakeStream:
    """Async-iterable + async-context-manager mimicking the SDK stream."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def __aiter__(self):
        async def _gen():
            for e in self._events:
                yield e

        return _gen()


def _provider_with_events(events) -> AnthropicProvider:
    p = AnthropicProvider(
        api_key="test-key",
        base_url="https://api.minimax.io/anthropic",
        disable_prompt_cache=True,
        default_model="MiniMax-M2.7",
    )

    # Replace the SDK messages.stream with our fake.
    def _fake_stream(**_kwargs):
        return _FakeStream(events)

    p._client = SimpleNamespace(  # type: ignore[assignment]
        messages=SimpleNamespace(stream=_fake_stream)
    )
    return p


async def _collect(provider, model):
    chunks = []
    async for c in provider.stream_chat(
        [LLMMessage(role="user", content="hi")],
        model=model,
        tools=[{"type": "function", "function": {"name": "noop", "parameters": {}}}],
    ):
        chunks.append(c)
    return chunks


@pytest.mark.asyncio
async def test_two_message_deltas_yield_single_done_and_single_count() -> None:
    """Two ``message_delta`` events MUST NOT double-fire the terminal chunk
    nor double-count the MiniMax counters."""
    events = [
        _message_start_event(),
        _text_delta_event(),
        _message_delta_event(),
        _message_delta_event(),  # a second one — the bug path
    ]
    provider = _provider_with_events(events)

    chunks = await _collect(provider, "MiniMax-M2.7")

    done_chunks = [c for c in chunks if getattr(c, "done", False)]
    assert len(done_chunks) == 1, f"expected exactly one done chunk, got {len(done_chunks)}"

    stats = provider.text_only_stats()
    # Exactly one MiniMax turn counted (the text-only-with-tools narration),
    # not two.
    assert stats["minimax_total_turns"] == 1
    assert stats["minimax_text_only_turns"] == 1


@pytest.mark.asyncio
async def test_single_message_delta_still_fires_once() -> None:
    """Regression guard: the normal one-``message_delta`` stream is unchanged."""
    events = [_message_start_event(), _text_delta_event(), _message_delta_event()]
    provider = _provider_with_events(events)

    chunks = await _collect(provider, "MiniMax-M2.7")

    done_chunks = [c for c in chunks if getattr(c, "done", False)]
    assert len(done_chunks) == 1
    stats = provider.text_only_stats()
    assert stats["minimax_total_turns"] == 1
    assert stats["minimax_text_only_turns"] == 1
