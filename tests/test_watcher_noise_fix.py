"""Tests for the watcher noise / UX overhaul (2026-05-17).

Covers the five behavioural changes wired this session:

* Browser-watcher sidecar drops ``document.title`` so flickering badges
  (``"WhatsApp (3)"`` → ``"WhatsApp (5)"``) stop bumping the diff hash.
* MCP watcher first-poll baseline: silently seeds ``last_seen_ids`` on
  the very first poll so a new watcher doesn't dump the entire backlog
  to Telegram.
* WhatsApp dedup: skips messages without a real upstream id rather than
  synthesizing ``{time}_{body}`` keys that re-fire when upstream flips
  formats.
* ``snoozed_until`` on either watcher type silently skips polls until
  the timestamp elapses.
* Beautiful WhatsApp formatter: contact-name resolution wins over raw
  JIDs, group rendering shows ``👥 Group · Sender``, single vs. digest
  layouts.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from lazyclaw.browser.watcher import (
    _JS_NAV_SIDECAR,
    build_watcher_context,
    is_check_due,
)
from lazyclaw.heartbeat.mcp_watcher import (
    _extract_new_items,
    _format_notification,
    build_mcp_watcher_context,
    check_mcp_watcher,
    is_mcp_check_due,
)


# ── Sidecar JS excludes title ────────────────────────────────────────


def test_sidecar_js_excludes_title():
    """document.title was the main false-positive source — confirm it's gone."""
    assert "document.title" not in _JS_NAV_SIDECAR
    assert "t:" not in _JS_NAV_SIDECAR.replace("test", "")  # paranoid
    # The remaining real fields are intentional.
    assert "m: messages" in _JS_NAV_SIDECAR
    assert "n: notifications" in _JS_NAV_SIDECAR
    assert "p: location.pathname" in _JS_NAV_SIDECAR


# ── Snooze blocks checks for both watcher types ──────────────────────


def test_browser_watcher_snooze_blocks_check():
    """``snoozed_until`` in the future suppresses is_check_due."""
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    ctx = {
        "snoozed_until": future,
        "check_interval": 1,
        "last_check": "2000-01-01T00:00:00+00:00",
    }
    assert is_check_due(ctx) is False


def test_browser_watcher_snooze_expired_lets_through():
    """Expired snooze must NOT permanently mute the watcher."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    ctx = {
        "snoozed_until": past,
        "check_interval": 1,
        "last_check": "2000-01-01T00:00:00+00:00",
    }
    assert is_check_due(ctx) is True


def test_browser_watcher_snooze_malformed_treated_as_unset():
    """A corrupted snoozed_until must NOT become snooze-forever."""
    ctx = {
        "snoozed_until": "not-a-real-timestamp",
        "check_interval": 1,
        "last_check": "2000-01-01T00:00:00+00:00",
    }
    assert is_check_due(ctx) is True


def test_mcp_watcher_snooze_blocks_check():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    ctx = {
        "snoozed_until": future,
        "check_interval": 1,
        "last_check": 0,
    }
    assert is_mcp_check_due(ctx) is False


def test_mcp_watcher_snooze_expired_lets_through():
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    ctx = {
        "snoozed_until": past,
        "check_interval": 1,
        "last_check": 0,
    }
    assert is_mcp_check_due(ctx) is True


# ── WhatsApp dedup requires real id ──────────────────────────────────


def test_whatsapp_dedup_skips_messages_without_id():
    """Synthesized ``{time}_{body}`` keys re-fire when upstream flips
    formats — formatter now requires a real Baileys id."""
    data = {
        "most_recent_messages": [
            {"id": "msg_real_1", "from": "111@s.whatsapp.net", "body": "hi"},
            # No id — must be skipped, NOT given a synthetic key.
            {"from": "222@s.whatsapp.net", "body": "no id here", "time": "12:00"},
            {"id": "msg_real_2", "from": "333@s.whatsapp.net", "body": "real"},
        ],
    }
    items = _extract_new_items(data, last_seen=set(), service="whatsapp")
    ids = [i["id"] for i in items]
    assert ids == ["msg_real_1", "msg_real_2"]


def test_whatsapp_dedup_skips_already_seen():
    data = {
        "most_recent_messages": [
            {"id": "msg_a", "from": "1@s.whatsapp.net", "body": "hi"},
            {"id": "msg_b", "from": "2@s.whatsapp.net", "body": "yo"},
        ],
    }
    items = _extract_new_items(data, last_seen={"msg_a"}, service="whatsapp")
    assert [i["id"] for i in items] == ["msg_b"]


def test_whatsapp_dedup_skips_self_sent():
    data = {
        "most_recent_messages": [
            {"id": "msg_self", "fromMe": True, "from": "me", "body": "ping"},
            {"id": "msg_other", "from": "2@s.whatsapp.net", "body": "yo"},
        ],
    }
    items = _extract_new_items(data, last_seen=set(), service="whatsapp")
    assert [i["id"] for i in items] == ["msg_other"]


# ── MCP watcher first-poll baseline ──────────────────────────────────


class _FakeMcpClient:
    """Tiny stand-in for an MCP client — just returns a canned JSON blob."""

    def __init__(self, response: str, name: str = "whatsapp"):
        self._response = response
        self.name = name
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name: str, args: dict) -> str:
        self.calls.append((tool_name, args))
        return self._response


@pytest.mark.asyncio
async def test_mcp_first_poll_silent_baseline():
    """First poll seeds last_seen_ids without firing a notification.

    Without this, ``last_seen_ids=[]`` makes every existing unread look
    "new" and the user's whole backlog dumps to Telegram on watcher
    creation. The seeded count is surfaced via ``_baseline_count`` so
    the daemon can show ONE friendly "watching now" push.
    """
    response = json.dumps({
        "most_recent_messages": [
            {"id": "m1", "from": "1@s.whatsapp.net", "body": "old 1"},
            {"id": "m2", "from": "2@s.whatsapp.net", "body": "old 2"},
            {"id": "m3", "from": "3@s.whatsapp.net", "body": "old 3"},
        ],
    })
    client = _FakeMcpClient(response, name="whatsapp")
    ctx = json.loads(build_mcp_watcher_context(
        service="whatsapp",
        tool_name="whatsapp_read",
        tool_args={"limit": 10},
    ))

    changed, notif, new_ctx = await check_mcp_watcher(
        ctx, {"wa": client},
    )
    assert changed is False
    assert notif is None
    assert new_ctx["_baseline_count"] == 3
    assert set(new_ctx["last_seen_ids"]) == {"m1", "m2", "m3"}


@pytest.mark.asyncio
async def test_mcp_second_poll_fires_only_on_new_messages():
    """After the silent baseline, only genuinely new ids notify."""
    # ── First (baseline) poll ──
    response1 = json.dumps({
        "most_recent_messages": [
            {"id": "old1", "from": "1@s.whatsapp.net", "body": "old"},
        ],
    })
    client = _FakeMcpClient(response1, name="whatsapp")
    ctx = json.loads(build_mcp_watcher_context(
        service="whatsapp", tool_name="whatsapp_read", tool_args={},
    ))
    _, _, ctx_after_baseline = await check_mcp_watcher(ctx, {"wa": client})
    assert "old1" in ctx_after_baseline["last_seen_ids"]

    # ── Second poll: one genuinely new message ──
    client._response = json.dumps({
        "most_recent_messages": [
            {"id": "old1", "from": "1@s.whatsapp.net", "body": "old"},
            {"id": "new1", "from": "2@s.whatsapp.net", "body": "FRESH"},
        ],
    })
    changed, notif, _ = await check_mcp_watcher(
        ctx_after_baseline, {"wa": client},
    )
    assert changed is True
    assert notif is not None
    assert "FRESH" in notif


# ── Beautiful formatter ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_whatsapp_format_uses_contact_resolver():
    """When a resolver is supplied, raw JIDs become saved contact names."""
    items = [{
        "id": "x",
        "from": "34611234567@s.whatsapp.net",
        "body": "Hola, ¿estás?",
        "timestamp": "2026-05-17 14:32:00 UTC",
        "type": "direct",
    }]

    async def _resolver(jid: str) -> str | None:
        return "Maria Vargas" if "611234567" in jid else None

    out = await _format_notification(
        items, "whatsapp", "", resolver=_resolver,
    )
    assert "Maria Vargas" in out
    # No JID gibberish should ever land in the user's chat.
    assert "@s.whatsapp.net" not in out
    assert "14:32" in out


@pytest.mark.asyncio
async def test_whatsapp_format_falls_back_to_phone_no_resolver():
    items = [{
        "id": "x",
        "from": "34611234567@s.whatsapp.net",
        "body": "Hi",
        "timestamp": "2026-05-17 09:05:00 UTC",
        "type": "direct",
    }]
    out = await _format_notification(items, "whatsapp", "", resolver=None)
    # Pretty-printed phone, NOT the raw JID.
    assert "@s.whatsapp.net" not in out
    assert "+34" in out


@pytest.mark.asyncio
async def test_whatsapp_format_group_layout():
    """Group messages render as `👥 Group · Sender: body`."""
    items = [{
        "id": "g1",
        "from": "999000-team@g.us",
        "type": "group",
        "groupName": "Family Group",
        "participantName": "Mom",
        "pushName": "Mom",
        "body": "Did you call dad?",
        "timestamp": "2026-05-17 18:00:00 UTC",
    }]
    out = await _format_notification(items, "whatsapp", "", resolver=None)
    assert "Family Group" in out
    assert "Mom" in out
    assert "Did you call dad?" in out


@pytest.mark.asyncio
async def test_whatsapp_format_digest_layout_caps_at_5_chats():
    """Digest layout caps at 5 chats with a clear `+N more` footer."""
    items = []
    for i in range(8):
        items.append({
            "id": f"m{i}",
            "from": f"chat{i}@s.whatsapp.net",
            "type": "direct",
            "body": f"msg {i}",
            "timestamp": f"2026-05-17 1{i}:00:00 UTC",
        })
    out = await _format_notification(items, "whatsapp", "", resolver=None)
    assert "8 new" in out
    assert "+3 more" in out


# ── Watcher keyboard builder ────────────────────────────────────────


def test_watcher_keyboard_layout_whatsapp():
    """WhatsApp watchers get [Mute] row + [⏰ 1h, 4h, 8h] snooze row."""
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon

    kb = HeartbeatDaemon._build_watcher_keyboard(
        job_id="abcdef0123456789",
        service="whatsapp",
        notified_items=[{
            "groupName": "Family Group",
            "from": "1@g.us",
            "type": "group",
        }],
    )
    # Two rows: mute, snooze.
    assert len(kb) == 2
    assert kb[0][0]["text"].endswith("Mute chat")
    assert all(b["callback_data"].startswith("wsnooze:") for b in kb[1])


def test_watcher_keyboard_callback_data_within_telegram_64b_limit():
    """Telegram hard-caps callback_data at 64 bytes — must hold."""
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon

    kb = HeartbeatDaemon._build_watcher_keyboard(
        job_id="abcdef0123456789",
        service="whatsapp",
        notified_items=[{
            "groupName": "A" * 200,  # absurdly long chat name
            "from": "1@g.us",
            "type": "group",
        }],
    )
    for row in kb:
        for btn in row:
            assert len(btn["callback_data"].encode("utf-8")) <= 64


def test_watcher_keyboard_browser_has_only_snooze_row():
    """Browser watchers don't have a chat to mute — snooze only."""
    from lazyclaw.heartbeat.daemon import HeartbeatDaemon

    kb = HeartbeatDaemon._build_watcher_keyboard(
        job_id="0011223344556677",
        service="browser",
        notified_items=[],
    )
    assert len(kb) == 1
    assert all(b["callback_data"].startswith("wsnooze:") for b in kb[0])


# ── build_watcher_context preserved its existing shape ───────────────


def test_build_watcher_context_still_carries_known_fields():
    raw = build_watcher_context(url="https://example.com")
    ctx = json.loads(raw)
    # Existing contract for upstream callers — don't drift.
    for field in ("url", "page_type", "custom_js", "check_interval",
                  "expires_at", "notify_template", "one_shot",
                  "accept_template_slug", "last_value", "last_check"):
        assert field in ctx
