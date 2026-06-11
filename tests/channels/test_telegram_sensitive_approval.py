"""Telegram ask-back for SENSITIVE_SKILL_DEFAULTS tools (2026-06-10 audit).

Before this, ``_TelegramCallback.on_approval_request`` auto-approved every
skill outside a tiny prefix denylist — so an ASK permission on a money
mover (``upwork_accept_offer`` / ``upwork_submit_milestone``) was silently
hollow on the user's primary channel. These tests pin the new behavior:
sensitive skills get a real inline-keyboard prompt and FAIL CLOSED on
timeout or send failure; everything else keeps the old auto-approve so no
existing Telegram flow gains a popup.
"""

from __future__ import annotations

import asyncio

import pytest

from lazyclaw.channels.telegram import (
    _PENDING_TOOL_APPROVALS,
    _TelegramCallback,
    resolve_tool_approval,
)

_CHAT_ID = 81001
_SERVER_UUID = "3f2a1b4c-9d8e-4f00-a1b2-c3d4e5f60789"


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail_send = False

    async def send_message(self, **kwargs):
        if self.fail_send:
            raise RuntimeError("telegram down")
        self.sent.append(kwargs)
        return object()


@pytest.fixture
def cb():
    bot = _FakeBot()
    callback = _TelegramCallback(bot, _CHAT_ID)
    yield callback, bot
    _PENDING_TOOL_APPROVALS.pop(_CHAT_ID, None)


# ── Unchanged behavior ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dangerous_prefix_still_denied(cb):
    callback, bot = cb
    assert await callback.on_approval_request("vault_set", {}) is False
    assert bot.sent == []


@pytest.mark.asyncio
async def test_non_sensitive_skill_still_auto_approved(cb):
    callback, bot = cb
    assert await callback.on_approval_request("upwork_get_messages", {}) is True
    assert bot.sent == []


# ── Sensitive skills: real ask-back ───────────────────────────────────


@pytest.mark.asyncio
async def test_sensitive_skill_sends_inline_keyboard_and_waits(cb):
    callback, bot = cb
    task = asyncio.ensure_future(
        callback.on_approval_request("upwork_accept_offer", {"offer_url": "x"})
    )
    await asyncio.sleep(0)  # let the prompt go out
    assert len(bot.sent) == 1
    assert bot.sent[0].get("reply_markup") is not None

    resolve_tool_approval(_CHAT_ID, approved=True)
    assert await task is True


@pytest.mark.asyncio
async def test_payment_skill_gets_ask_back_not_auto_approve(cb):
    callback, bot = cb
    task = asyncio.ensure_future(
        callback.on_approval_request("payment", {"action": "get_card"})
    )
    await asyncio.sleep(0)
    assert len(bot.sent) == 1  # prompt sent, NOT auto-approved
    resolve_tool_approval(_CHAT_ID, approved=False)
    assert await task is False


@pytest.mark.asyncio
async def test_sensitive_skill_reject_button_denies(cb):
    callback, _ = cb
    task = asyncio.ensure_future(
        callback.on_approval_request("upwork_submit_milestone", {})
    )
    await asyncio.sleep(0)
    resolve_tool_approval(_CHAT_ID, approved=False)
    assert await task is False


@pytest.mark.asyncio
async def test_sensitive_skill_matches_dynamic_mcp_id(cb):
    callback, bot = cb
    task = asyncio.ensure_future(
        callback.on_approval_request(
            f"mcp_{_SERVER_UUID}_upwork_accept_offer", {}
        )
    )
    await asyncio.sleep(0)
    assert len(bot.sent) == 1
    resolve_tool_approval(_CHAT_ID, approved=True)
    assert await task is True


@pytest.mark.asyncio
async def test_sensitive_skill_times_out_fail_closed(cb, monkeypatch):
    monkeypatch.setattr(
        "lazyclaw.channels.telegram._TOOL_APPROVAL_TIMEOUT", 0.01
    )
    callback, _ = cb
    assert await callback.on_approval_request("upwork_accept_offer", {}) is False


@pytest.mark.asyncio
async def test_send_failure_fails_closed(cb):
    callback, bot = cb
    bot.fail_send = True
    assert await callback.on_approval_request("upwork_accept_offer", {}) is False


def test_resolve_without_pending_is_noop():
    resolve_tool_approval(999999, approved=True)  # must not raise


# ── Adapter callback handler (button taps must actually resolve) ──────


class _FakeMessage:
    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id
        self.edited: list[str] = []

    async def edit_reply_markup(self, reply_markup=None):
        self.edited.append("markup_cleared")


class _FakeQuery:
    def __init__(self, data: str, chat_id: int) -> None:
        self.data = data
        self.message = _FakeMessage(chat_id)
        self.answered = False
        self.edited_text: str | None = None

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.edited_text = text


class _FakeUpdate:
    def __init__(self, query) -> None:
        self.callback_query = query


def _make_adapter(allowed_chat: int):
    from lazyclaw.channels.telegram import TelegramAdapter

    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._is_allowed = lambda chat_id: str(chat_id) == str(allowed_chat)
    return adapter


@pytest.mark.asyncio
async def test_approval_button_yes_resolves_pending_future():
    adapter = _make_adapter(_CHAT_ID)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _PENDING_TOOL_APPROVALS[_CHAT_ID] = future

    query = _FakeQuery("apprv:yes", _CHAT_ID)
    await adapter._handle_approval_callback(_FakeUpdate(query), None)

    assert query.answered is True
    assert future.done() and future.result() is True
    _PENDING_TOOL_APPROVALS.pop(_CHAT_ID, None)


@pytest.mark.asyncio
async def test_approval_button_no_denies_pending_future():
    adapter = _make_adapter(_CHAT_ID)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _PENDING_TOOL_APPROVALS[_CHAT_ID] = future

    query = _FakeQuery("apprv:no", _CHAT_ID)
    await adapter._handle_approval_callback(_FakeUpdate(query), None)

    assert future.done() and future.result() is False
    _PENDING_TOOL_APPROVALS.pop(_CHAT_ID, None)


@pytest.mark.asyncio
async def test_approval_button_from_unauthorized_chat_ignored():
    adapter = _make_adapter(allowed_chat=_CHAT_ID)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    _PENDING_TOOL_APPROVALS[_CHAT_ID] = future

    intruder_chat = 555
    query = _FakeQuery("apprv:yes", intruder_chat)
    await adapter._handle_approval_callback(_FakeUpdate(query), None)

    assert not future.done()
    _PENDING_TOOL_APPROVALS.pop(_CHAT_ID, None)


def test_apprv_handler_is_registered_before_catchall():
    """The apprv: pattern must be wired in _register_handlers — an
    unregistered pattern means decorative buttons (task-button lesson)."""
    import inspect as _inspect

    from lazyclaw.channels.telegram import TelegramAdapter

    source = _inspect.getsource(TelegramAdapter)
    assert 'pattern=r"^apprv:"' in source
