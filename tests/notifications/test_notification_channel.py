"""Tests for notification channel routing (lazyclaw/notifications/channel.py).

Covers: pure gating decisions (telegram | app | both), channel normalization,
default resolution, set/get round-trip, and admin-user resolution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications import channel as chan

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


# ── Pure gating decisions ──────────────────────────────────────────────

async def test_gating_telegram_sends_telegram_only():
    assert chan.should_send_telegram("telegram") is True
    assert chan.should_record_feed("telegram") is False


async def test_gating_app_records_feed_only():
    assert chan.should_send_telegram("app") is False
    assert chan.should_record_feed("app") is True


async def test_gating_both_does_both():
    assert chan.should_send_telegram("both") is True
    assert chan.should_record_feed("both") is True


async def test_normalize_unknown_falls_back_to_telegram():
    assert chan.normalize_channel("garbage") == "telegram"
    assert chan.normalize_channel(None) == "telegram"
    assert chan.normalize_channel("  APP  ") == "app"
    # Unknown channels are safe (telegram-biased) under both gates.
    assert chan.should_send_telegram("garbage") is True
    assert chan.should_record_feed("garbage") is False


# ── Persistence ────────────────────────────────────────────────────────

async def test_default_channel_is_telegram(cfg):
    assert await chan.get_notification_channel(cfg, "u1") == "telegram"


async def test_set_and_get_round_trip(cfg):
    result = await chan.set_notification_channel(cfg, "u1", "both")
    assert result == "both"
    assert await chan.get_notification_channel(cfg, "u1") == "both"

    await chan.set_notification_channel(cfg, "u1", "app")
    assert await chan.get_notification_channel(cfg, "u1") == "app"


async def test_set_rejects_invalid_channel(cfg):
    with pytest.raises(ValueError):
        await chan.set_notification_channel(cfg, "u1", "carrier-pigeon")


async def test_set_preserves_other_settings(cfg):
    # Pre-seed an unrelated settings key; setting the channel must not nuke it.
    import json

    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps({"eco": {"mode": "claude"}}), "u1"),
        )
        await db.commit()

    await chan.set_notification_channel(cfg, "u1", "app")

    async with db_session(cfg) as db:
        cur = await db.execute("SELECT settings FROM users WHERE id = 'u1'")
        row = await cur.fetchone()
    settings = json.loads(row[0])
    assert settings["eco"]["mode"] == "claude", "unrelated settings must survive"
    assert settings["notifications"]["channel"] == "app"


async def test_resolve_admin_user_id_returns_first_user(cfg):
    assert await chan.resolve_admin_user_id(cfg) == "u1"


async def test_resolver_prefers_telegram_bound_user(cfg):
    """Real installs accumulate stale rows ('default' from first boot, test
    users). The OWNER is whoever holds the Telegram admin-chat binding —
    picking by created_at alone routed every preference read AND feed write
    to the stale first row (2026-06-10 incident: user toggled 'App only' on
    their real account; notifications kept reading/writing 'default')."""
    import json

    async with db_session(cfg) as db:
        # u1 (first registered) stays unbound; the REAL user registers later
        # and carries the chat binding.
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt, "
            "settings, created_at) VALUES (?, ?, ?, ?, ?, datetime('now', '+1 day'))",
            ("u-real", "blck", "x", "salt-b",
             json.dumps({"telegram_chat_id": "8127631458"})),
        )
        await db.commit()

    assert await chan.resolve_admin_user_id(cfg) == "u-real"


async def test_resolver_falls_back_to_first_user_when_unbound(cfg):
    # No telegram_chat_id anywhere → legacy first-registered behaviour.
    assert await chan.resolve_admin_user_id(cfg) == "u1"
