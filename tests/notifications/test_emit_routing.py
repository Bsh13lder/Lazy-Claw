"""Tests for the two centralized emit funnels honouring the channel switch.

Funnel 1: lazyclaw.notifications.push.push_telegram (direct skill/heartbeat path)
Funnel 2: lazyclaw.notifications.telegram_notifier.TelegramNotifier.on_event
          (AgentCallback path)

Both must: record to the feed for app/both, skip the Telegram send for app,
and leave the feed untouched for telegram. No Telegram token is configured in
these tests, so the real send path is naturally inert — we assert on the feed
side + the gating outcome.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications import channel as chan
from lazyclaw.notifications import feed_store
from lazyclaw.notifications.push import push_telegram
from lazyclaw.notifications.telegram_notifier import TelegramNotifier

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


async def _feed_count(cfg) -> int:
    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    return len(feed["notifications"])


# ── Funnel 1: push_telegram ────────────────────────────────────────────

async def test_push_app_records_and_skips_telegram(cfg):
    await chan.set_notification_channel(cfg, "u1", "app")
    # app → treated as delivered even with no Telegram configured.
    sent = await push_telegram(cfg, "hello from a skill")
    assert sent is True, "app-mode push must report delivered"
    assert await _feed_count(cfg) == 1

    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    assert feed["notifications"][0]["body"] == "hello from a skill"
    assert feed["notifications"][0]["kind"] == "push"


async def test_push_telegram_only_records_nothing(cfg):
    # Default channel is telegram. No token configured → returns False,
    # and crucially nothing lands in the feed.
    sent = await push_telegram(cfg, "telegram-only message")
    assert sent is False
    assert await _feed_count(cfg) == 0


async def test_push_both_records_feed(cfg):
    await chan.set_notification_channel(cfg, "u1", "both")
    await push_telegram(cfg, "goes to both")
    assert await _feed_count(cfg) == 1


# ── Funnel 2: TelegramNotifier.on_event ────────────────────────────────

class _FakeBot:
    def __init__(self):
        self.sends: list[dict] = []

    async def send_message(self, **kwargs):
        self.sends.append(kwargs)


def _bg_failed_event():
    # background_failed produces text straight from metadata (no work_summary
    # priming needed) and is not source=="brain", so _format returns text.
    return SimpleNamespace(
        kind="background_failed",
        metadata={"name": "nightly", "error": "boom"},
    )


async def test_notifier_app_records_feed_and_skips_send(cfg):
    await chan.set_notification_channel(cfg, "u1", "app")
    bot = _FakeBot()
    notifier = TelegramNotifier(
        bot=bot, admin_chat_id_fn=lambda: "123", config=cfg,
    )
    await notifier.on_event(_bg_failed_event())

    assert bot.sends == [], "app-mode must NOT send to Telegram"
    assert await _feed_count(cfg) == 1
    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    n = feed["notifications"][0]
    assert n["kind"] == "background_failed"
    assert "boom" in n["body"]
    assert "<" not in n["body"], "feed body must be plain text, not HTML"


async def test_notifier_both_sends_and_records(cfg):
    await chan.set_notification_channel(cfg, "u1", "both")
    bot = _FakeBot()
    notifier = TelegramNotifier(
        bot=bot, admin_chat_id_fn=lambda: "123", config=cfg,
    )
    await notifier.on_event(_bg_failed_event())

    assert len(bot.sends) == 1, "both-mode must send to Telegram"
    assert await _feed_count(cfg) == 1


async def test_notifier_telegram_sends_and_records_feed(cfg):
    # Default telegram channel. Spine (2026-07-16): discrete task events are
    # recorded durably ALWAYS; telegram mode still sends AND now records.
    bot = _FakeBot()
    notifier = TelegramNotifier(
        bot=bot, admin_chat_id_fn=lambda: "123", config=cfg,
    )
    await notifier.on_event(_bg_failed_event())

    assert len(bot.sends) == 1
    assert await _feed_count(cfg) == 1


async def test_notifier_without_config_is_telegram_only(cfg):
    # Back-compat: notifier built without config (legacy call sites) keeps the
    # exact old behavior — Telegram send, never touches the feed.
    await chan.set_notification_channel(cfg, "u1", "app")
    bot = _FakeBot()
    notifier = TelegramNotifier(bot=bot, admin_chat_id_fn=lambda: "123")
    await notifier.on_event(_bg_failed_event())

    assert len(bot.sends) == 1, "no-config notifier ignores the channel switch"
    assert await _feed_count(cfg) == 0


async def test_notifier_suppressed_event_records_nothing(cfg):
    # source==brain background_failed is suppressed by _format → no feed entry
    # for app mode either (only what would have gone to Telegram is recorded).
    await chan.set_notification_channel(cfg, "u1", "app")
    bot = _FakeBot()
    notifier = TelegramNotifier(
        bot=bot, admin_chat_id_fn=lambda: "123", config=cfg,
    )
    ev = SimpleNamespace(
        kind="background_failed",
        metadata={"name": "x", "error": "y", "source": "brain"},
    )
    await notifier.on_event(ev)

    assert bot.sends == []
    assert await _feed_count(cfg) == 0
