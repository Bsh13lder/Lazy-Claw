"""A model content-policy refusal must surface as a distinct, typed outcome.

2026-08-23: a background dispatch to record the user's OWN admin panel was
refused by claude-opus-5's cybersecurity safeguard. The SDK reported it as a
``ResultError`` (⊂ ``ProcessError``) whose message carried the API error text
"claude-opus-5's safeguards flagged this message for a cybersecurity topic".

That is neither a login problem (``SDKUnavailable``) nor the benign max_turns=1
stop, so it fell to the catch-all ``else`` and became a generic
``RuntimeError("Claude SDK process error: ...")`` — which the runtime rendered
to the user as an opaque "brain stalled" hiccup, with no hint of what was
refused or the sanctioned exemption path.

A content-policy refusal is its own thing: not a transport failure to fall back
over, and NOT something to retry on a different model (a safety refusal is a
model-family property, so model-shopping is both futile and an evasion
anti-pattern). These tests pin that it raises a typed ``ContentPolicyRefusal``,
distinct from every other branch.
"""

from __future__ import annotations

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import AssistantMessage, TextBlock  # noqa: E402
from claude_agent_sdk._errors import ProcessError  # noqa: E402

from lazyclaw.llm.providers.base import LLMMessage  # noqa: E402
from lazyclaw.llm.providers.claude_sdk_provider import (  # noqa: E402
    ClaudeSDKProvider,
    ContentPolicyRefusal,
    SDKUnavailable,
)

pytestmark = pytest.mark.asyncio

# The verbatim shape of the 2026-08-23 refusal.
_REFUSAL = (
    "Claude Code returned an error result: API Error: claude-opus-5's "
    "safeguards flagged this message for a cybersecurity topic. If your work "
    "requires this access, you can apply for an exemption: "
    "https://claude.com/form/cyber-use-case"
)
_MSGS = [LLMMessage(role="user", content="hi")]


class ResultError(ProcessError):
    """Stand-in for `claude_agent_sdk._errors.ResultError` (0.2.x-only class).

    The handler must key on the SHAPE — a ProcessError subclass whose message
    carries the refusal text — not on the concrete class, so that is what these
    tests assert.
    """


def _provider():
    return ClaudeSDKProvider(model="opus", claude_bin="/bin/true")


def _patch_query(monkeypatch, blocks, raise_exc):
    import claude_agent_sdk

    async def _fake_query(*, prompt, options):  # noqa: ARG001
        if blocks:
            yield AssistantMessage(content=blocks, model="opus")
        raise raise_exc

    monkeypatch.setattr(claude_agent_sdk, "query", _fake_query)


async def test_content_policy_refusal_raises_typed(monkeypatch):
    """The cyber-flag refusal raises ContentPolicyRefusal, not a generic error."""
    _patch_query(monkeypatch, [], ResultError(_REFUSAL, exit_code=1))
    with pytest.raises(ContentPolicyRefusal):
        await _provider().chat(_MSGS, "opus")


async def test_content_policy_refusal_is_not_sdkunavailable(monkeypatch):
    """It must NOT be treated as a transport failure (no silent CLI fallback)."""
    _patch_query(monkeypatch, [], ResultError(_REFUSAL, exit_code=1))
    with pytest.raises(ContentPolicyRefusal) as exc:
        await _provider().chat(_MSGS, "opus")
    assert not isinstance(exc.value, SDKUnavailable)


async def test_content_policy_message_names_the_exemption(monkeypatch):
    """The raised error must carry an actionable exemption pointer for the UI."""
    _patch_query(monkeypatch, [], ResultError(_REFUSAL, exit_code=1))
    with pytest.raises(ContentPolicyRefusal) as exc:
        await _provider().chat(_MSGS, "opus")
    text = str(exc.value).lower()
    assert "exemption" in text or "cyber" in text


async def test_generic_process_error_is_not_content_policy(monkeypatch):
    """A real subprocess failure stays a generic RuntimeError, not a refusal."""
    _patch_query(
        monkeypatch, [], ProcessError("something actually broke", exit_code=127)
    )
    with pytest.raises(RuntimeError) as exc:
        await _provider().chat(_MSGS, "opus")
    assert not isinstance(exc.value, ContentPolicyRefusal)


async def test_login_still_takes_priority(monkeypatch):
    """'not logged in' must still map to SDKUnavailable, not a refusal."""
    _patch_query(
        monkeypatch, [], ProcessError("not logged in — run /login", exit_code=1)
    )
    with pytest.raises(SDKUnavailable):
        await _provider().chat(_MSGS, "opus")


async def test_answered_turn_then_refusal_text_is_not_hijacked(monkeypatch):
    """If the model actually answered, a trailing max_turns stop is still
    swallowed — the refusal classifier must not fire on benign output."""
    _patch_query(
        monkeypatch,
        [TextBlock(text="Here is your answer.")],
        ResultError(
            "Claude Code returned an error result: Reached maximum number of "
            "turns (1)",
            exit_code=1,
        ),
    )
    out = await _provider().chat(_MSGS, "opus")
    assert out.content == "Here is your answer."


async def test_streaming_refusal_also_raises_typed(monkeypatch):
    """The stream path must type the refusal too, not raise a bare error."""
    _patch_query(monkeypatch, [], ResultError(_REFUSAL, exit_code=1))
    with pytest.raises(ContentPolicyRefusal):
        async for _ in _provider().stream_chat(_MSGS, "opus"):
            pass
