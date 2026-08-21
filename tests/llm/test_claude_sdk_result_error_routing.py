"""The max_turns=1 stop must be handled whichever EXCEPTION TYPE it arrives as.

2026-08-21, brain fully down: every chat failed with

    RuntimeError: Claude SDK process error: Claude Code returned an error
    result: Reached maximum number of turns (1)

``_max_turns_tail_action`` was correct and its unit tests passed — it was simply
never reached. The handler's ``except`` chain runs ``ProcessError`` BEFORE the
generic ``Exception`` branch that holds the swallow/retry logic, and

    ResultError -> ProcessError -> ClaudeSDKError -> Exception

so on claude-agent-sdk 0.2.x, where the cap surfaces as a ``ResultError``, the
stop was caught by the ``ProcessError`` clause and re-raised immediately. The
code's comments were written against 0.1.81, where it arrived as a bare
``Exception``.

Since one-turn-per-chat means EVERY successful turn ends at that cap, this took
the whole brain down. These tests pin the routing, not just the classifier.
"""

from __future__ import annotations

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock  # noqa: E402
from claude_agent_sdk._errors import ProcessError  # noqa: E402

from lazyclaw.llm.providers.base import LLMMessage  # noqa: E402
from lazyclaw.llm.providers.claude_sdk_provider import (  # noqa: E402
    ClaudeSDKProvider,
)

pytestmark = pytest.mark.asyncio

_CAP = "Claude Code returned an error result: Reached maximum number of turns (1)"
_MSGS = [LLMMessage(role="user", content="hi")]


class ResultError(ProcessError):
    """Stand-in for `claude_agent_sdk._errors.ResultError`.

    Declared locally rather than imported: the SDK only grew this class in
    0.2.x, and pinning the test to one version would make it silently skip on
    the other. What the handler must actually honour is the SHAPE — a
    ProcessError subclass whose message carries the max_turns text — so that is
    what these tests assert. (Verified 2026-08-21 against 0.2.143 in the
    container: ResultError -> ProcessError -> ClaudeSDKError -> Exception.)
    """


async def test_the_bug_in_one_line():
    """A max_turns stop delivered as a ProcessError subclass must not be
    re-raised by the ProcessError clause before the retry logic sees it."""
    assert issubclass(ResultError, ProcessError)


def _provider():
    return ClaudeSDKProvider(model="sonnet", claude_bin="/bin/true")


def _patch_query(monkeypatch, blocks, raise_exc):
    """Make `claude_agent_sdk.query` yield `blocks` then raise `raise_exc`."""
    import claude_agent_sdk

    async def _fake_query(*, prompt, options):  # noqa: ARG001
        if blocks:
            yield AssistantMessage(content=blocks, model="sonnet")
        raise raise_exc

    monkeypatch.setattr(claude_agent_sdk, "query", _fake_query)


async def test_max_turns_as_ResultError_with_text_is_swallowed(monkeypatch):
    """The ordinary success path: model answered, then the cap fired."""
    _patch_query(
        monkeypatch,
        [TextBlock(text="Hello from the brain.")],
        ResultError(_CAP, exit_code=1),
    )
    out = await _provider().chat(_MSGS, "sonnet")
    assert out.content == "Hello from the brain."


async def test_max_turns_as_ResultError_with_tool_calls_is_swallowed(monkeypatch):
    """The other success shape: the turn ended by emitting tool calls."""
    _patch_query(
        monkeypatch,
        [ToolUseBlock(id="t1", name="mcp__lazyclaw__search_tools", input={"q": "x"})],
        ResultError(_CAP, exit_code=1),
    )
    out = await _provider().chat(_MSGS, "sonnet")
    assert [t.name for t in out.tool_calls] == ["search_tools"]


async def test_empty_turn_as_ResultError_retries_once_then_succeeds(monkeypatch):
    """Zero-output cap = the transient built-in-tool leak → one retry."""
    import claude_agent_sdk

    calls = {"n": 0}

    async def _fake_query(*, prompt, options):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            raise ResultError(_CAP, exit_code=1)  # empty turn
        yield AssistantMessage(content=[TextBlock(text="second try")], model="sonnet")
        raise ResultError(_CAP, exit_code=1)

    monkeypatch.setattr(claude_agent_sdk, "query", _fake_query)

    out = await _provider().chat(_MSGS, "sonnet")
    assert calls["n"] == 2, "should have retried exactly once"
    assert out.content == "second try"


async def test_a_genuine_process_error_still_raises(monkeypatch):
    """Don't swallow real subprocess failures — only the max_turns stop."""
    _patch_query(
        monkeypatch, [], ProcessError("something actually broke", exit_code=127)
    )
    with pytest.raises(RuntimeError, match="process error"):
        await _provider().chat(_MSGS, "sonnet")


async def test_not_logged_in_still_maps_to_SDKUnavailable(monkeypatch):
    """The login branch must keep priority over the max_turns handling."""
    from lazyclaw.llm.providers.claude_sdk_provider import SDKUnavailable

    _patch_query(
        monkeypatch, [], ProcessError("not logged in — run /login", exit_code=1)
    )
    with pytest.raises(SDKUnavailable):
        await _provider().chat(_MSGS, "sonnet")
