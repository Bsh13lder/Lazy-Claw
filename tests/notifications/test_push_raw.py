"""Tests for the extracted _send_telegram_raw helper.

_send_telegram_raw must: resolve token + chat_id, truncate, build inline keyboard,
and call Bot.send_message — WITHOUT touching the notification feed.
These tests run without telegram installed; the no-token branch is reachable
purely from missing TELEGRAM_ADMIN_CHAT.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, init_db
from lazyclaw.notifications import push as push_mod

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    try:
        yield c
    finally:
        await close_pool()


async def test_send_raw_no_token_returns_false(monkeypatch, config):
    # No telegram admin chat configured -> raw send returns False, never touches feed.
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT", raising=False)
    ok = await push_mod._send_telegram_raw(config, "hello", parse_mode=None)
    assert ok is False


async def test_send_raw_no_config_returns_false(monkeypatch):
    # config=None -> no token -> False.
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT", raising=False)
    ok = await push_mod._send_telegram_raw(None, "hello")
    assert ok is False


async def test_send_raw_does_not_call_route_to_feed(monkeypatch, config):
    """_send_telegram_raw must never call _route_to_feed_or_skip."""
    called = []

    async def _fake_route(cfg, text):
        called.append(True)
        return True, False

    monkeypatch.setattr(push_mod, "_route_to_feed_or_skip", _fake_route)
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT", raising=False)

    await push_mod._send_telegram_raw(config, "hello")
    assert called == [], "_send_telegram_raw must NOT call _route_to_feed_or_skip"
