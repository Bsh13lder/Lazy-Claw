"""Tests for the NL ask-conversation trigger (match + start helpers).

Covers:
  - match_ask_conversation: regex correctness + negative cases
  - start_ask_conversation: contact-resolve + conversation_runner.start call,
    handle-resolution paths, graceful error strings
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lazyclaw.runtime.instant_dispatch import (
    match_ask_conversation,
    start_ask_conversation,
)


# ── match_ask_conversation ────────────────────────────────────────────────────

def test_matches_ask_on_whatsapp():
    m = match_ask_conversation("ask Alice on WhatsApp if she's coming to my birthday")
    assert m is not None
    assert m["channel"] == "whatsapp"
    assert "birthday" in m["goal"]


def test_matches_via_email():
    m = match_ask_conversation("ask Bob via email whether the invoice was paid")
    assert m["channel"] == "email"
    assert m["who"] == "Bob"


def test_matches_instagram_and_telegram():
    assert match_ask_conversation("ask Sam on instagram if he saw my reel")["channel"] == "instagram"
    assert match_ask_conversation("ask Mia via telegram to send the file")["channel"] == "telegram"


def test_no_match_plain_message():
    assert match_ask_conversation("what's the weather") is None
    assert match_ask_conversation("ask me later") is None  # no channel anchor


# ── start_ask_conversation ────────────────────────────────────────────────────

def _make_contact(name: str, whatsapp_jid: str | None = None, handles: dict | None = None):
    """Build a minimal ContactRecord-like object."""
    record = MagicMock()
    record.name_canonical = name
    record.handles = handles or {}
    if whatsapp_jid is not None:
        record.whatsapp_jid.return_value = whatsapp_jid
    else:
        record.whatsapp_jid.return_value = None
    return record


def _stub_conversation_runner() -> types.ModuleType:
    """Return a stub lazyclaw.comms.conversation_runner module."""
    mod = types.ModuleType("lazyclaw.comms.conversation_runner")
    mod.start = AsyncMock()
    return mod


@pytest.mark.asyncio
async def test_start_ask_resolves_whatsapp_jid():
    """Happy path: contact found with a whatsapp_jid — runner.start called with it."""
    config = object()
    user_id = "u1"
    match = {"who": "Alice", "channel": "whatsapp", "goal": "if she's coming"}

    contact = _make_contact("Alice", whatsapp_jid="+34600111222@s.whatsapp.net")
    runner_mod = _stub_conversation_runner()

    with (
        patch(
            "lazyclaw.contacts.store.find_contact",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.dict(sys.modules, {
            "lazyclaw.comms.conversation_runner": runner_mod,
            "lazyclaw.comms": types.ModuleType("lazyclaw.comms"),
        }),
    ):
        reply = await start_ask_conversation(config, user_id, match)

    runner_mod.start.assert_awaited_once()
    call_kwargs = runner_mod.start.call_args
    assert call_kwargs.kwargs["channel"] == "whatsapp"
    assert call_kwargs.kwargs["contact"] == "+34600111222@s.whatsapp.net"
    assert call_kwargs.kwargs["goal"] == "if she's coming"
    assert "Alice" in reply
    assert "whatsapp" in reply.lower()


@pytest.mark.asyncio
async def test_start_ask_falls_back_to_raw_name_when_no_handle():
    """Contact found but no handle for this channel — raw name used as contact."""
    config = object()
    user_id = "u2"
    match = {"who": "Bob", "channel": "email", "goal": "paid the invoice?"}

    # email handle absent in handles dict
    contact = _make_contact("Bob", handles={"whatsapp": "+34600000001"})
    runner_mod = _stub_conversation_runner()

    with (
        patch(
            "lazyclaw.contacts.store.find_contact",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.dict(sys.modules, {
            "lazyclaw.comms.conversation_runner": runner_mod,
            "lazyclaw.comms": types.ModuleType("lazyclaw.comms"),
        }),
    ):
        reply = await start_ask_conversation(config, user_id, match)

    runner_mod.start.assert_awaited_once()
    call_kwargs = runner_mod.start.call_args
    # canonical name used as contact fallback when handle missing
    assert call_kwargs.kwargs["contact"] == "Bob"
    assert "Bob" in reply


@pytest.mark.asyncio
async def test_start_ask_falls_back_to_raw_name_when_contact_not_found():
    """No contact record found — raw 'who' string forwarded as contact."""
    config = object()
    user_id = "u3"
    match = {"who": "Unknown Person", "channel": "telegram", "goal": "when are they free"}

    runner_mod = _stub_conversation_runner()

    with (
        patch(
            "lazyclaw.contacts.store.find_contact",
            new=AsyncMock(return_value=[]),
        ),
        patch.dict(sys.modules, {
            "lazyclaw.comms.conversation_runner": runner_mod,
            "lazyclaw.comms": types.ModuleType("lazyclaw.comms"),
        }),
    ):
        reply = await start_ask_conversation(config, user_id, match)

    runner_mod.start.assert_awaited_once()
    call_kwargs = runner_mod.start.call_args
    assert call_kwargs.kwargs["contact"] == "Unknown Person"
    assert "Unknown Person" in reply


@pytest.mark.asyncio
async def test_start_ask_returns_error_string_when_runner_unavailable():
    """ImportError on conversation_runner → graceful error string, no raise."""
    config = object()
    user_id = "u4"
    match = {"who": "Alice", "channel": "whatsapp", "goal": "coming?"}

    with (
        patch(
            "lazyclaw.contacts.store.find_contact",
            new=AsyncMock(return_value=[]),
        ),
        patch.dict(sys.modules, {
            "lazyclaw.comms.conversation_runner": None,  # triggers ImportError
        }),
    ):
        reply = await start_ask_conversation(config, user_id, match)

    assert "available" in reply.lower() or "wrong" in reply.lower()
    assert "Alice" in reply or "whatsapp" in reply.lower()


@pytest.mark.asyncio
async def test_start_ask_returns_error_string_when_runner_raises():
    """runner.start raising an exception → graceful error string, no raise."""
    config = object()
    user_id = "u5"
    match = {"who": "Carol", "channel": "instagram", "goal": "saw the post?"}

    runner_mod = _stub_conversation_runner()
    runner_mod.start = AsyncMock(side_effect=RuntimeError("connection lost"))

    with (
        patch(
            "lazyclaw.contacts.store.find_contact",
            new=AsyncMock(return_value=[]),
        ),
        patch.dict(sys.modules, {
            "lazyclaw.comms.conversation_runner": runner_mod,
            "lazyclaw.comms": types.ModuleType("lazyclaw.comms"),
        }),
    ):
        reply = await start_ask_conversation(config, user_id, match)

    # Should be an error message, not raise
    assert isinstance(reply, str)
    assert "wrong" in reply.lower() or "error" in reply.lower() or "sorry" in reply.lower()
