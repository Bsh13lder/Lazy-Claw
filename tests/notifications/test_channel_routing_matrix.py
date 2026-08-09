"""Channel-routing matrix — telegram | app | both × feed / telegram / chat / realtime.

Design (2026-08 "proactive pings in chat"):
  * feed row      → ALWAYS (spine contract, every funnel);
  * telegram send → channel ∈ {telegram, both};
  * chat card     → channel ∈ {app, both} (or forced via spine's
                    ``chat_card=True`` param);
  * realtime WS   → spine: always (when not silent); legacy funnels: under
                    the same {app, both} gate as the chat card.

Covers: spine.notify, deliver_heartbeat_push, push_telegram,
TelegramNotifier (+ Prefixed Class-A live hint), and the pure
``should_send_chat`` helper.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.notifications import channel as chan
from lazyclaw.notifications import feed_store, spine
from lazyclaw.notifications.heartbeat_push import deliver_heartbeat_push
from lazyclaw.notifications.push import push_telegram
from lazyclaw.notifications.telegram_notifier import (
    PrefixedTelegramNotifier,
    TelegramNotifier,
)
from lazyclaw.runtime.session_resolver import invalidate_primary_session

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
    invalidate_primary_session("u1")
    try:
        yield c
    finally:
        invalidate_primary_session("u1")
        await close_pool()


@pytest.fixture
def legs(monkeypatch):
    """Spy on both app-transport legs (chat card + realtime frame)."""
    chat = AsyncMock(return_value="msg-1")
    rt = AsyncMock(return_value=None)
    monkeypatch.setattr("lazyclaw.notifications.chat_card.emit", chat)
    monkeypatch.setattr("lazyclaw.notifications.realtime.emit", rt)
    return SimpleNamespace(chat=chat, realtime=rt)


async def _feed_count(cfg) -> int:
    feed = await feed_store.get_notifications_since(cfg, "u1", None)
    return len(feed["notifications"])


# ── pure helper ────────────────────────────────────────────────────────


async def test_should_send_chat_matrix():
    assert chan.should_send_chat("telegram") is False
    assert chan.should_send_chat("app") is True
    assert chan.should_send_chat("both") is True
    assert chan.should_send_chat("garbage") is False  # fail-safe default


# ── spine.notify ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "channel,expect_tg,expect_chat",
    [
        ("telegram", True, False),
        ("app", False, True),
        ("both", True, True),
    ],
)
async def test_spine_matrix(cfg, legs, monkeypatch, channel, expect_tg, expect_chat):
    await chan.set_notification_channel(cfg, "u1", channel)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(spine, "_send_telegram_raw", sent)

    await spine.notify(cfg, "u1", kind="task_reminder", title="t", body="b")

    assert await _feed_count(cfg) == 1, "feed row is unconditional"
    assert sent.await_count == (1 if expect_tg else 0)
    assert legs.chat.await_count == (1 if expect_chat else 0)
    # Spine publishes the realtime frame for every channel (UI-only hint).
    assert legs.realtime.await_count == 1
    if expect_chat:
        notif = legs.chat.await_args.args[2]
        assert notif["kind"] == "task_reminder"
        assert notif["id"], "chat leg receives the recorded feed row"


async def test_spine_chat_card_param_forces_chat_leg(cfg, legs, monkeypatch):
    await chan.set_notification_channel(cfg, "u1", "telegram")
    monkeypatch.setattr(spine, "_send_telegram_raw", AsyncMock(return_value=True))
    await spine.notify(
        cfg, "u1", kind="approval_needed", title="t", body="b", chat_card=True,
    )
    assert legs.chat.await_count == 1, "chat_card=True must force the chat leg"


async def test_spine_silent_suppresses_all_fanout(cfg, legs, monkeypatch):
    await chan.set_notification_channel(cfg, "u1", "both")
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(spine, "_send_telegram_raw", sent)
    await spine.notify(cfg, "u1", kind="info", title="t", body="b", silent=True)
    assert await _feed_count(cfg) == 1
    sent.assert_not_awaited()
    legs.chat.assert_not_awaited()
    legs.realtime.assert_not_awaited()


# ── deliver_heartbeat_push ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "channel,expect_tg,expect_app",
    [
        ("telegram", True, False),
        ("app", False, True),
        ("both", True, True),
    ],
)
async def test_heartbeat_push_matrix(cfg, legs, channel, expect_tg, expect_app):
    await chan.set_notification_channel(cfg, "u1", channel)
    tg = AsyncMock()
    await deliver_heartbeat_push(
        cfg, "⏰ Medicine due", telegram_send=tg, kind="task_reminder",
    )
    assert await _feed_count(cfg) == 1, "feed row is unconditional"
    assert tg.await_count == (1 if expect_tg else 0)
    assert legs.chat.await_count == (1 if expect_app else 0)
    assert legs.realtime.await_count == (1 if expect_app else 0)
    if expect_app:
        notif = legs.chat.await_args.args[2]
        assert notif["kind"] == "task_reminder"
        assert notif["body"] == "⏰ Medicine due"


# ── push_telegram ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "channel,expect_app",
    [("telegram", False), ("app", True), ("both", True)],
)
async def test_push_telegram_matrix(cfg, legs, channel, expect_app):
    await chan.set_notification_channel(cfg, "u1", channel)
    await push_telegram(cfg, "**skill** ping")
    assert await _feed_count(cfg) == 1, "feed row is unconditional (gap fixed)"
    assert legs.chat.await_count == (1 if expect_app else 0)
    assert legs.realtime.await_count == (1 if expect_app else 0)
    if expect_app:
        notif = legs.chat.await_args.args[2]
        assert notif["kind"] == "push"
        assert "**" not in notif["body"], "markdown flattened for the card"


# ── TelegramNotifier ───────────────────────────────────────────────────


class _FakeBot:
    def __init__(self):
        self.sends: list[dict] = []

    async def send_message(self, **kwargs):
        self.sends.append(kwargs)


def _bg_failed_event(**meta_over):
    meta = {"name": "nightly", "error": "boom"}
    meta.update(meta_over)
    return SimpleNamespace(kind="background_failed", metadata=meta)


def _bg_done_event(**meta_over):
    meta = {"name": "nightly", "result": "42 rows scraped"}
    meta.update(meta_over)
    return SimpleNamespace(kind="background_done", metadata=meta)


async def test_notifier_app_bg_failed_gets_chat_card_and_frame(cfg, legs):
    await chan.set_notification_channel(cfg, "u1", "app")
    notifier = TelegramNotifier(
        bot=_FakeBot(), admin_chat_id_fn=lambda: "123", config=cfg,
    )
    await notifier.on_event(_bg_failed_event())
    assert legs.chat.await_count == 1
    assert legs.realtime.await_count == 1
    notif = legs.chat.await_args.args[2]
    assert notif["kind"] == "background_failed"
    assert "boom" in notif["body"]


async def test_notifier_telegram_channel_skips_app_legs(cfg, legs):
    # Default channel = telegram.
    notifier = TelegramNotifier(
        bot=_FakeBot(), admin_chat_id_fn=lambda: "123", config=cfg,
    )
    await notifier.on_event(_bg_failed_event())
    legs.chat.assert_not_awaited()
    legs.realtime.assert_not_awaited()


async def test_notifier_bg_done_is_realtime_only(cfg, legs):
    # The bg task's own Agent.process_message turn already persisted its
    # reply to the primary session — a chat card would duplicate it.
    await chan.set_notification_channel(cfg, "u1", "app")
    notifier = TelegramNotifier(
        bot=_FakeBot(), admin_chat_id_fn=lambda: "123", config=cfg,
    )
    await notifier.on_event(_bg_done_event())
    legs.chat.assert_not_awaited()
    assert legs.realtime.await_count == 1


async def test_notifier_fanout_member_never_chat_cards(cfg, legs):
    # Consolidation-duplicate suppression: a task in a registered brain
    # fan-out group gets ONE merged reply from the consolidator turn.
    await chan.set_notification_channel(cfg, "u1", "app")
    notifier = TelegramNotifier(
        bot=_FakeBot(), admin_chat_id_fn=lambda: "123", config=cfg,
    )
    await notifier.on_event(_bg_failed_event(fanout_group_id="grp-1"))
    legs.chat.assert_not_awaited()


async def test_prefixed_done_publishes_class_a_hint_not_chat_card(cfg, legs):
    # Class-A: cron/reminder turns persist their reply via agent.py already.
    # The Prefixed notifier publishes a realtime hint (title = job name)
    # and must NOT write a chat card.
    await chan.set_notification_channel(cfg, "u1", "app")
    notifier = PrefixedTelegramNotifier(
        bot=_FakeBot(), admin_chat_id_fn=lambda: "123",
        prefix="survival_message_check", config=cfg,
    )
    summary = SimpleNamespace(
        result_full="James sent 2 new messages about the BPO bot.",
        result_preview=None, duration_ms=1200, llm_calls=1,
        total_tokens=100, total_cost=0.0, tools_used=["upwork_inbox_check"],
    )
    await notifier.on_event(
        SimpleNamespace(kind="work_summary", metadata={"summary": summary}),
    )
    await notifier.on_event(SimpleNamespace(kind="done", metadata={}))

    legs.chat.assert_not_awaited()
    assert legs.realtime.await_count == 1
    hint = legs.realtime.await_args.args[2]
    assert hint["kind"] == "done"
    assert hint["title"] == "survival_message_check"
    assert "James sent 2 new messages" in hint["body"]
    assert hint["id"], "hint should carry the feed row id when recorded"
