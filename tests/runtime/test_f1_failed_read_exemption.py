"""F1 failed-read exemption (2026-06-24 11:15 loop).

When a channel-read tool ERRORS (auth failure, empty inbox, traceback) it
returns an error envelope, not messages — so there is nothing to quote and
the brain's F1 grounding gate must NOT demand a `> sender (ts):` quote block.
Before this fix, ``_check_f1_violation`` treated the error string as
"non-empty content" and forced retries until exhaustion, shipping
``[F1-accepted-degraded]`` after a 7-iteration loop.
"""

from __future__ import annotations

import pytest

from lazyclaw.runtime.agent import (
    _check_f1_violation,
    _tool_result_has_quotable_content,
)

_SUBSTANTIVE = "x" * 400  # well over _F1_DRAFT_QUOTE_THRESHOLD_CHARS, no quote line


@pytest.mark.parametrize(
    "tr",
    [
        "Error in email_read: b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'",
        "[AUTHENTICATIONFAILED] Invalid credentials",
        "Error in whatsapp_read: not logged in",
        "No conversations found, make sure you're logged in",
        "no new messages available",
        "Login expired — please re-authenticate",
        "Could not connect to the IMAP server",
        "session expired",
        "Traceback (most recent call last):\n  File ...",
        "",
        "   ",
        None,
    ],
)
def test_error_envelopes_are_not_quotable(tr):
    assert _tool_result_has_quotable_content(tr) is False


@pytest.mark.parametrize(
    "tr",
    [
        "> James Blue (7:08 PM): I have to run out to work",
        "James Blue: lets finish our things tomorrow",
        '{"messages": [{"from": "Vato", "text": "heyy"}]}',
        "Here is the conversation: Vato said hello and asked about the slot.",
        # mentions an error word inside real content — still quotable
        "Vato: my login failed yesterday but it works now, lets proceed",
    ],
)
def test_real_message_content_is_quotable(tr):
    assert _tool_result_has_quotable_content(tr) is True


def test_failed_read_does_not_trigger_violation():
    """THE FIX: substantive reply + only-error tool results => no violation."""
    tool_results = [
        "Error in email_read: b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'",
    ]
    assert _check_f1_violation(_SUBSTANTIVE, tool_results) is None


def test_successful_read_still_requires_quote_block():
    """Unchanged: a real read + substantive reply without a quote => violation."""
    tool_results = ["> James Blue (7:08 PM): I have to run out to work"]
    reason = _check_f1_violation(_SUBSTANTIVE, tool_results)
    assert reason is not None
    assert "quote block" in reason


def test_mixed_results_one_good_read_still_enforces():
    """If ANY read returned quotable content, F1 still enforces."""
    tool_results = [
        "Error in email_read: [AUTHENTICATIONFAILED]",
        "> Vato (6:09 PM): lets finish our things",
    ]
    assert _check_f1_violation(_SUBSTANTIVE, tool_results) is not None


def test_banned_phrase_still_fails_even_on_failed_read():
    """Banned 'from memory' phrasing fails regardless of read success."""
    draft = "Already pulled the thread above — " + "y" * 400
    reason = _check_f1_violation(draft, ["Error in email_read: [AUTHENTICATIONFAILED]"])
    assert reason is not None
    assert "banned phrase" in reason


def test_short_ack_never_violates():
    """Short acknowledgments make no claims => never a violation."""
    assert _check_f1_violation("ok, done", ["> Vato (6:09 PM): hi"]) is None


def test_empty_draft_passes():
    assert _check_f1_violation("", ["> Vato (6:09 PM): hi"]) is None
