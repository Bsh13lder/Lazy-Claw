"""A content-policy refusal renders an actionable card, not the "brain" hiccup.

The provider raises a typed ``ContentPolicyRefusal``; the runtime must classify
it (even when re-wrapped through the router's ``__cause__`` chain) and render
the dedicated card that names the exemption path — never the opaque "brain
stalled" message and never the raw exception text.
"""

from __future__ import annotations

from lazyclaw.channels.telegram_view import render_error
from lazyclaw.runtime.agent import _is_content_policy_exception


def test_typed_refusal_is_detected():
    from lazyclaw.llm.providers.claude_sdk_provider import ContentPolicyRefusal

    exc = ContentPolicyRefusal("declined: cyber-use-case exemption at ...")
    assert _is_content_policy_exception(exc) is True


def test_refusal_detected_through_cause_chain():
    from lazyclaw.llm.providers.claude_sdk_provider import ContentPolicyRefusal

    inner = ContentPolicyRefusal("safeguards flagged this message")
    try:
        try:
            raise inner
        except ContentPolicyRefusal as e:
            raise RuntimeError("router wrapped it") from e
    except RuntimeError as wrapped:
        assert _is_content_policy_exception(wrapped) is True


def test_string_fallback_when_untyped():
    exc = RuntimeError(
        "Claude Code returned an error result: API Error: safeguards flagged "
        "this message for a cybersecurity topic"
    )
    assert _is_content_policy_exception(exc) is True


def test_ordinary_error_is_not_content_policy():
    assert _is_content_policy_exception(RuntimeError("connection reset")) is False
    assert _is_content_policy_exception(RuntimeError("429 rate limit")) is False


def test_card_names_the_exemption_and_hides_internals():
    card = render_error("content_policy")
    assert "cyber-use-case" in card
    assert "brain stalled" not in card
    # It must not echo raw provider/exception text.
    assert "Traceback" not in card and "ResultError" not in card
