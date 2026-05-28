"""Tests for the Telegram ✅ Accept inline-keyboard callback (PR 5).

Covers:
  - ``build_watcher_context`` carries ``accept_template_slug``
  - ``WatchSiteSkill`` accepts + forwards ``accept_template_slug``
  - ``push_telegram`` inline_keyboard plumbing (mocked Bot) — passes
    InlineKeyboardMarkup, clamps callback_data to 64 bytes, ignores
    malformed button dicts
  - ``contract_intake_executor`` passes ``slug`` to ``watch_site`` so
    the watcher push gets the buttons
  - ``_handle_callback`` ``accept:<slug>`` branch: finds template,
    sets active, edits the message to confirm
  - ``accept:skip:<slug>`` branch: no template invocation, message
    updated to "skipped"
  - Missing template surfaces an error edit instead of crashing
  - Unauthorized chat is rejected silently (no template invocation)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lazyclaw.browser.watcher import build_watcher_context


# ── build_watcher_context carries the slug ───────────────────────────


def test_watcher_context_no_slug_default():
    raw = build_watcher_context(url="https://x.com")
    ctx = json.loads(raw)
    assert ctx["accept_template_slug"] is None


def test_watcher_context_with_slug():
    raw = build_watcher_context(
        url="https://x.com",
        accept_template_slug="fieldnation_com",
    )
    ctx = json.loads(raw)
    assert ctx["accept_template_slug"] == "fieldnation_com"


# ── push_telegram inline_keyboard ────────────────────────────────────


@pytest.mark.asyncio
async def test_push_telegram_with_keyboard(monkeypatch):
    """When inline_keyboard is passed, Bot.send_message receives
    reply_markup=InlineKeyboardMarkup(...)."""
    from lazyclaw.notifications import push as push_mod

    captured = {}

    class _FakeBot:
        def __init__(self, token):
            captured["token"] = token

        async def send_message(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT", "12345")
    monkeypatch.setattr(push_mod, "Bot", _FakeBot, raising=False)
    # Need to inject Bot into the import path used inside push_telegram
    import telegram as _tg
    monkeypatch.setattr(_tg, "Bot", _FakeBot)

    class _Cfg:
        telegram_bot_token = "fake-token"

    ok = await push_mod.push_telegram(
        _Cfg(), "test message",
        parse_mode=None,
        inline_keyboard=[[
            {"text": "✅ Accept", "callback_data": "accept:slug1"},
            {"text": "⏭ Skip", "callback_data": "accept:skip:slug1"},
        ]],
    )
    assert ok is True
    assert captured["reply_markup"] is not None
    # Markup has the rows we passed
    rm = captured["reply_markup"]
    assert hasattr(rm, "inline_keyboard")
    assert len(rm.inline_keyboard) == 1
    assert len(rm.inline_keyboard[0]) == 2
    assert rm.inline_keyboard[0][0].text == "✅ Accept"
    assert rm.inline_keyboard[0][0].callback_data == "accept:slug1"


@pytest.mark.asyncio
async def test_push_telegram_keyboard_clamps_callback_data(monkeypatch):
    """callback_data > 64 bytes must be truncated (Telegram hard cap)."""
    from lazyclaw.notifications import push as push_mod

    captured = {}

    class _FakeBot:
        def __init__(self, token): pass
        async def send_message(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT", "1")
    import telegram as _tg
    monkeypatch.setattr(_tg, "Bot", _FakeBot)

    class _Cfg:
        telegram_bot_token = "x"

    long_cd = "accept:" + ("a" * 200)
    await push_mod.push_telegram(
        _Cfg(), "x",
        inline_keyboard=[[{"text": "go", "callback_data": long_cd}]],
    )
    rm = captured["reply_markup"]
    assert len(rm.inline_keyboard[0][0].callback_data.encode()) <= 64


@pytest.mark.asyncio
async def test_push_telegram_keyboard_ignores_malformed(monkeypatch):
    """Buttons missing text or callback_data are silently skipped."""
    from lazyclaw.notifications import push as push_mod

    captured = {}

    class _FakeBot:
        def __init__(self, token): pass
        async def send_message(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT", "1")
    import telegram as _tg
    monkeypatch.setattr(_tg, "Bot", _FakeBot)

    class _Cfg:
        telegram_bot_token = "x"

    await push_mod.push_telegram(
        _Cfg(), "x",
        inline_keyboard=[[
            {"text": "ok", "callback_data": "good"},
            {"text": "missing_cd"},        # no callback_data → drop
            {"callback_data": "no_text"},   # no text → drop
            "not_a_dict",                   # wrong type → drop
        ]],
    )
    rm = captured["reply_markup"]
    # Only the first valid button survives
    assert len(rm.inline_keyboard[0]) == 1
    assert rm.inline_keyboard[0][0].text == "ok"


@pytest.mark.asyncio
async def test_push_telegram_no_keyboard_no_markup(monkeypatch):
    """Plain text push must NOT include reply_markup."""
    from lazyclaw.notifications import push as push_mod

    captured = {}

    class _FakeBot:
        def __init__(self, token): pass
        async def send_message(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT", "1")
    import telegram as _tg
    monkeypatch.setattr(_tg, "Bot", _FakeBot)

    class _Cfg:
        telegram_bot_token = "x"

    await push_mod.push_telegram(_Cfg(), "plain")
    assert captured["reply_markup"] is None


# ── _handle_callback accept: branch ──────────────────────────────────


@pytest.fixture
async def tmp_config(tmp_path: Path):
    from lazyclaw.config import Config
    from lazyclaw.db.connection import close_pool, db_session, init_db

    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


def _make_commands_handler(config, allowed_chat="100"):
    """Construct a minimal TelegramCommands instance for callback testing."""
    from lazyclaw.channels.telegram_commands import TelegramCommands
    # The class needs an _adapter with _allowed_chats + a resolve method.
    fake_adapter = MagicMock()
    fake_adapter._allowed_chats = {allowed_chat}
    handler = TelegramCommands(
        adapter=fake_adapter,
        config=config,
        agent=MagicMock(),
        task_runner=None,
        team_lead=None,
    )
    # Resolve all chats to user u1 for testing
    handler._resolve_user = AsyncMock(return_value="u1")
    return handler


def _make_query(callback_data: str, chat_id: str = "100"):
    """Build a fake telegram.CallbackQuery-shaped object."""
    q = MagicMock()
    q.data = callback_data
    q.message.chat_id = int(chat_id)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    return q


@pytest.mark.asyncio
async def test_accept_skip_branch(tmp_config):
    """accept:skip:<slug> just edits the message — no template lookup."""
    handler = _make_commands_handler(tmp_config)
    update = MagicMock()
    update.callback_query = _make_query("accept:skip:fieldnation_com")
    await handler._handle_callback(update, MagicMock())
    update.callback_query.edit_message_text.assert_called_once()
    args, _ = update.callback_query.edit_message_text.call_args
    assert "Skipped" in args[0]
    assert "fieldnation_com" in args[0]


@pytest.mark.asyncio
async def test_accept_unauthorized_chat_silently_dropped(tmp_config):
    handler = _make_commands_handler(tmp_config, allowed_chat="999")
    update = MagicMock()
    update.callback_query = _make_query("accept:fieldnation_com", chat_id="100")
    await handler._handle_callback(update, MagicMock())
    # No edit — unauthorized
    update.callback_query.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_accept_missing_template_graceful(tmp_config):
    """accept:<slug> where template doesn't exist → user-friendly error."""
    handler = _make_commands_handler(tmp_config)
    update = MagicMock()
    update.callback_query = _make_query("accept:nonexistent_slug")
    await handler._handle_callback(update, MagicMock())
    update.callback_query.edit_message_text.assert_called_once()
    args, _ = update.callback_query.edit_message_text.call_args
    assert "not found" in args[0]
    assert "nonexistent_slug_accept" in args[0]


@pytest.mark.asyncio
async def test_accept_existing_template_sets_active(tmp_config):
    """Happy path: template exists → set_active called → confirmation edit."""
    # Pre-create a template
    from lazyclaw.browser import templates as tpl_store
    tpl = await tpl_store.create_template(
        tmp_config, "u1",
        name="fieldnation_com_accept",
        playbook="click the green accept button",
        setup_urls=["https://www.fieldnation.com/jobs"],
        checkpoints=["Confirm accept"],
    )

    handler = _make_commands_handler(tmp_config)
    update = MagicMock()
    update.callback_query = _make_query("accept:fieldnation_com")

    # Patch active_templates.set_active to capture
    from lazyclaw.browser import active_templates
    set_calls: list = []
    orig = active_templates.set_active

    def _spy(user_id, tpl_id):
        set_calls.append((user_id, tpl_id))
        orig(user_id, tpl_id)

    active_templates.set_active = _spy
    try:
        await handler._handle_callback(update, MagicMock())
    finally:
        active_templates.set_active = orig

    # Active template was set to our tpl
    assert set_calls == [("u1", tpl["id"])]
    # Confirmation message edited
    update.callback_query.edit_message_text.assert_called_once()
    args, _ = update.callback_query.edit_message_text.call_args
    assert "Accept fired" in args[0]
    assert "fieldnation_com_accept" in args[0]


# ── _handle_callback top-level auth gate (destructive branches) ──────


def _patch_db_spy(monkeypatch):
    """Patch telegram_commands.db_session so we can assert no DELETE runs.

    Returns the list that captures every SQL string passed to db.execute.
    The destructive branches import db_session lazily from
    lazyclaw.db.connection, so we patch it at the source module.
    """
    from lazyclaw.db import connection as conn_mod

    executed: list[str] = []

    class _FakeDB:
        async def execute(self, sql, *args, **kwargs):
            executed.append(sql)
            return MagicMock()

        async def commit(self):
            pass

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeDB()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(conn_mod, "db_session", lambda cfg: _FakeCtx())
    return executed


@pytest.mark.asyncio
async def test_nuke_all_unauthorized_chat_no_deletion(tmp_config, monkeypatch):
    """An unauthorized chat firing nuke:all must NOT delete anything."""
    executed = _patch_db_spy(monkeypatch)
    handler = _make_commands_handler(tmp_config, allowed_chat="999")
    update = MagicMock()
    update.callback_query = _make_query("nuke:all", chat_id="100")

    await handler._handle_callback(update, MagicMock())

    # Gate fired before the branch: no SQL, no edit, no resolve.
    assert executed == []
    assert not any("DELETE" in s.upper() for s in executed)
    update.callback_query.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_wipe_confirm_unauthorized_chat_no_deletion(tmp_config, monkeypatch):
    """An unauthorized chat firing wipe:confirm must NOT clear history."""
    executed = _patch_db_spy(monkeypatch)
    handler = _make_commands_handler(tmp_config, allowed_chat="999")
    update = MagicMock()
    update.callback_query = _make_query("wipe:confirm", chat_id="100")

    await handler._handle_callback(update, MagicMock())

    assert not any("DELETE" in s.upper() for s in executed)
    update.callback_query.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_wipe_confirm_authorized_chat_deletes(tmp_config, monkeypatch):
    """Authorized chat keeps existing behavior: wipe:confirm clears history."""
    executed = _patch_db_spy(monkeypatch)
    handler = _make_commands_handler(tmp_config, allowed_chat="100")
    update = MagicMock()
    update.callback_query = _make_query("wipe:confirm", chat_id="100")

    await handler._handle_callback(update, MagicMock())

    assert any(
        "DELETE FROM agent_messages" in s for s in executed
    ), "authorized wipe:confirm should delete agent_messages"
    update.callback_query.edit_message_text.assert_called_once()


# ── watch_site forwards accept_template_slug ─────────────────────────


@pytest.mark.asyncio
async def test_watch_site_persists_slug_in_context(tmp_config, monkeypatch):
    """Calling watch_site with accept_template_slug must end up in the
    agent_jobs row's context blob."""
    from lazyclaw.skills.builtin.watcher_skills import WatchSiteSkill
    skill = WatchSiteSkill(config=tmp_config)
    # Skip the LLM extractor + browser auto-launch
    async def _no_js(*a, **k): return None
    monkeypatch.setattr(skill, "_generate_extractor_js", _no_js)
    async def _no_browser(*a, **k): return None
    monkeypatch.setattr(skill, "_ensure_browser", _no_browser)

    await skill.execute("u1", {
        "url": "https://www.example.com/jobs",
        "what_to_watch": "new items",
        "check_interval_minutes": 2,
        "duration_hours": 1,
        "custom_js": "(() => 'sentinel')()",
        "accept_template_slug": "example_com",
    })

    # Fetch the watcher row and confirm the slug is in the encrypted ctx
    from lazyclaw.crypto.encryption import decrypt, is_encrypted
    from lazyclaw.crypto.key_manager import get_user_dek
    from lazyclaw.db.connection import db_session

    key = await get_user_dek(tmp_config, "u1")
    async with db_session(tmp_config) as db:
        cursor = await db.execute(
            "SELECT context FROM agent_jobs WHERE user_id=? AND job_type='watcher'",
            ("u1",),
        )
        row = await cursor.fetchone()

    assert row is not None
    raw = decrypt(row[0], key) if is_encrypted(row[0]) else row[0]
    ctx = json.loads(raw)
    assert ctx["accept_template_slug"] == "example_com"


@pytest.mark.asyncio
async def test_watch_site_default_no_slug(tmp_config, monkeypatch):
    """When accept_template_slug is omitted, ctx['accept_template_slug'] is None."""
    from lazyclaw.skills.builtin.watcher_skills import WatchSiteSkill
    skill = WatchSiteSkill(config=tmp_config)
    async def _no_js(*a, **k): return None
    monkeypatch.setattr(skill, "_generate_extractor_js", _no_js)
    async def _no_browser(*a, **k): return None
    monkeypatch.setattr(skill, "_ensure_browser", _no_browser)

    await skill.execute("u1", {
        "url": "https://www.example.com/jobs",
        "what_to_watch": "new items",
        "custom_js": "(() => 'x')()",
    })

    from lazyclaw.crypto.encryption import decrypt, is_encrypted
    from lazyclaw.crypto.key_manager import get_user_dek
    from lazyclaw.db.connection import db_session

    key = await get_user_dek(tmp_config, "u1")
    async with db_session(tmp_config) as db:
        cursor = await db.execute(
            "SELECT context FROM agent_jobs WHERE user_id=? AND job_type='watcher'",
            ("u1",),
        )
        row = await cursor.fetchone()
    ctx = json.loads(decrypt(row[0], key) if is_encrypted(row[0]) else row[0])
    assert ctx["accept_template_slug"] is None
