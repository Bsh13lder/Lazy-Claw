"""Tests for the orphan-bubble continuation fix (2026-05-23).

The bug being closed: Upwork sometimes splits a long contact message
across multiple sibling DOM bubbles where only the FIRST carries the
story-header. The CONTINUATION bubbles arrive with body content but
no resolvable sender. Before this fix, the brain quoted those orphans
with "(no timestamp, no sender):" attribution and dressed them up as
authoritative contact content.

Today's incident: James Blue's 10:37 PM spec list was split into
  bubble[0]: sender="James Blue", ts="10:37 PM", body="I want to give
             you the overal expectation of the program:"
  bubble[1]: sender=None, ts=None, body="* Securely log into...
             (30-line spec)"
The brain quoted bubble[1] as if it were a standalone authoritative
quote. The new ``_resolve_orphan_bubble`` helper attaches it to the
prior contact bubble's content.
"""

from __future__ import annotations

import logging

import pytest

from upwork_mcp.tools.messages import _resolve_orphan_bubble


def _contact_msg(sender: str, ts: str, content: str) -> dict:
    return {
        "sender": sender,
        "timestamp": ts,
        "content": content,
        "is_mine": False,
    }


def _my_msg(content: str) -> dict:
    return {
        "sender": "Me",
        "timestamp": "1:00 PM",
        "content": content,
        "is_mine": True,
    }


def _orphan(content: str) -> dict:
    return {"content": content}


# ─── attach paths ────────────────────────────────────────────────────────


def test_orphan_after_contact_concatenates_with_newline():
    prior = [_contact_msg("James", "10:37 PM", "I want to give you:")]
    action = _resolve_orphan_bubble(_orphan("* item 1 * item 2"), prior)
    assert action == "attached"
    assert len(prior) == 1
    assert prior[0]["content"] == "I want to give you:\n* item 1 * item 2"
    # Sender/timestamp preserved on the prior bubble.
    assert prior[0]["sender"] == "James"
    assert prior[0]["timestamp"] == "10:37 PM"


def test_two_consecutive_orphans_both_concatenate_to_prior_contact():
    prior = [_contact_msg("James", "10:37 PM", "a")]
    _resolve_orphan_bubble(_orphan("b"), prior)
    _resolve_orphan_bubble(_orphan("c"), prior)
    assert len(prior) == 1
    assert prior[0]["content"] == "a\nb\nc"


def test_orphan_attaches_to_contact_even_when_prior_body_empty():
    """If the FIRST bubble of a multi-part message had no body and
    only the continuation has the content, the attach should still
    work without injecting a leading newline."""
    prior = [_contact_msg("James", "10:37 PM", "")]
    _resolve_orphan_bubble(_orphan("real content"), prior)
    assert prior[0]["content"] == "real content"


# ─── drop paths ──────────────────────────────────────────────────────────


def test_orphan_after_my_bubble_is_dropped():
    prior = [_my_msg("ok thanks")]
    action = _resolve_orphan_bubble(_orphan("orphan garbage"), prior)
    assert action == "dropped"
    # My bubble is not mutated.
    assert prior[0]["content"] == "ok thanks"
    assert prior[0]["is_mine"] is True


def test_orphan_with_no_predecessor_is_dropped():
    prior: list[dict] = []
    action = _resolve_orphan_bubble(_orphan("orphan"), prior)
    assert action == "dropped"
    assert prior == []


def test_orphan_with_empty_body_is_dropped():
    prior = [_contact_msg("James", "10:37 PM", "real content")]
    action = _resolve_orphan_bubble(_orphan(""), prior)
    assert action == "dropped"
    # Prior is untouched.
    assert prior[0]["content"] == "real content"


def test_orphan_with_whitespace_only_body_is_dropped():
    prior = [_contact_msg("James", "10:37 PM", "real")]
    action = _resolve_orphan_bubble(_orphan("   \n\t  "), prior)
    assert action == "dropped"
    assert prior[0]["content"] == "real"


# ─── adversarial: predecessor with missing sender is NOT attach target ──


def test_orphan_does_not_attach_to_predecessor_lacking_sender():
    """A prior bubble with `is_mine=False` but no resolved sender
    is itself orphan-shaped — we mustn't chain orphans onto it."""
    prior = [{"is_mine": False, "content": "something", "sender": None}]
    action = _resolve_orphan_bubble(_orphan("orphan body"), prior)
    assert action == "dropped"
    assert prior[0]["content"] == "something"


def test_orphan_does_not_attach_when_predecessor_is_mine_None():
    """`is_mine=None` (couldn't determine) is NOT the same as False.
    We require an explicit foreign predecessor."""
    prior = [{"sender": "Maybe James", "content": "something", "is_mine": None}]
    action = _resolve_orphan_bubble(_orphan("orphan body"), prior)
    assert action == "dropped"


# ─── log emission ────────────────────────────────────────────────────────


def test_attach_logs_at_info_with_prior_sender_and_bytecount(caplog):
    prior = [_contact_msg("James Blue", "10:37 PM", "header line")]
    with caplog.at_level(logging.INFO, logger="upwork_mcp.tools.messages"):
        _resolve_orphan_bubble(_orphan("attached body"), prior)
    assert any(
        "orphan bubble attached" in r.message for r in caplog.records
    )


def test_drop_logs_at_warning_with_body_preview(caplog):
    prior: list[dict] = []
    with caplog.at_level(logging.WARNING, logger="upwork_mcp.tools.messages"):
        _resolve_orphan_bubble(_orphan("orphan content that should appear in log"), prior)
    assert any(
        "dropping orphan bubble" in r.message for r in caplog.records
    )
