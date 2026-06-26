"""MiniMax per-request timeout → fallback escalation.

A single MiniMax-M3 call hung ~9 minutes on 2026-06-24 (SDK default timeout
~600s). The MiniMax client now gets a bounded timeout (config
minimax_timeout_s, default 150s); a hung call raises a timeout exception
which _is_timeout_exception catches so the agent escalates to the fallback
model — the same path as a rate-limit 429.
"""

from __future__ import annotations

import asyncio

import pytest

from lazyclaw.runtime.agent import (
    _is_rate_limit_exception,
    _is_timeout_exception,
)


# Stand-ins whose CLASS NAME carries "timeout" — mirrors anthropic.APITimeoutError
# / httpx.ReadTimeout without constructing the real (request-arg-heavy) types.
class APITimeoutError(Exception):
    pass


class ReadTimeout(Exception):
    pass


class ConnectTimeout(Exception):
    pass


@pytest.mark.parametrize(
    "exc",
    [
        APITimeoutError("Request timed out."),
        ReadTimeout("read timed out"),
        ConnectTimeout("connect timed out"),
        asyncio.TimeoutError(),
        TimeoutError("operation timed out"),
        Exception("Request timed out after 150.0s"),
        Exception("httpx.ReadTimeout: The read operation timeout"),
    ],
)
def test_timeout_detected(exc):
    assert _is_timeout_exception(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        Exception("rate_limit_error (2062)"),
        Exception("429 Too Many Requests"),
        Exception("authentication failed"),
        ValueError("bad argument"),
        Exception("some unrelated error"),
    ],
)
def test_non_timeout_not_detected(exc):
    assert _is_timeout_exception(exc) is False


def test_rate_limit_and_timeout_are_distinct():
    rl = Exception("rate_limit_error (2062)")
    to = APITimeoutError("timed out")
    assert _is_rate_limit_exception(rl) is True
    assert _is_timeout_exception(rl) is False
    assert _is_timeout_exception(to) is True
    assert _is_rate_limit_exception(to) is False


def test_provider_passes_timeout_and_max_retries():
    from lazyclaw.llm.providers.anthropic_provider import AnthropicProvider

    p = AnthropicProvider(
        api_key="test-key",
        base_url="https://api.minimax.io/anthropic",
        timeout=150.0,
        max_retries=1,
    )
    # The anthropic SDK exposes the configured values on the client.
    assert p._client.timeout == 150.0
    assert p._client.max_retries == 1


def test_provider_without_timeout_uses_sdk_default():
    from lazyclaw.llm.providers.anthropic_provider import AnthropicProvider

    p = AnthropicProvider(api_key="test-key")
    # No override → SDK default (real Anthropic stays unbounded). Just confirm
    # construction succeeded and our 150s bound was NOT applied.
    assert p._client is not None
    assert p._client.timeout != 150.0


def test_config_has_minimax_timeout_default():
    from lazyclaw.config import Config

    assert Config().minimax_timeout_s == 150.0
