"""Tests for /api/inbox HTTP routes (Task C4).

Auth approach: FastAPI dependency_overrides[get_current_user] = lambda: _fake_user()
and dependency_overrides[load_config] = lambda: cfg — exactly the same pattern as
test_mobile_settings.py.

Registry / gateway: we monkeypatch `lazyclaw.gateway.routes.inbox.build_gateway`
so the direct-reply and messages endpoints don't need a live MCP runtime.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.comms import thread_store
from lazyclaw.comms.models import Msg, SendResult
from lazyclaw.db.connection import db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.inbox import router as inbox_router
from lazyclaw.config import load_config


# ── helpers ────────────────────────────────────────────────────────────────────


async def _setup_db(tmp_path: Path) -> Config:
    """Create a minimal DB with one user and the channel_threads table."""
    cfg = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "hashed", "salt-a"),
        )
        await db.commit()
    return cfg


def _fake_user() -> User:
    return User(id="u1", username="alice", display_name=None,
                encryption_salt="salt-a", role="user")


def _make_app(cfg: Config) -> FastAPI:
    app = FastAPI()
    app.include_router(inbox_router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[load_config] = lambda: cfg
    return app


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def inbox_client(tmp_path: Path):
    cfg = await _setup_db(tmp_path)
    return TestClient(_make_app(cfg))


@pytest.fixture
async def inbox_client_with_thread(tmp_path: Path):
    """Client pre-seeded with one thread for alice."""
    cfg = await _setup_db(tmp_path)
    thread = await thread_store.upsert_thread(
        cfg, "u1",
        channel="whatsapp",
        contact_handle="+34612345678",
        contact_name="Bob",
        preview="Hey there",
        increment_unread=True,
    )
    app = _make_app(cfg)
    return TestClient(app), thread, cfg


# ── GET /api/inbox/threads ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_threads_empty(inbox_client) -> None:
    """GET /api/inbox/threads returns empty list for a new user."""
    r = inbox_client.get("/api/inbox/threads")
    assert r.status_code == 200
    body = r.json()
    assert body["threads"] == []
    assert body["count"] == 0


@pytest.mark.asyncio
async def test_list_threads_after_upsert(inbox_client_with_thread) -> None:
    """GET /api/inbox/threads returns the seeded thread."""
    client, thread, _cfg = inbox_client_with_thread
    r = client.get("/api/inbox/threads")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert len(body["threads"]) == 1
    t = body["threads"][0]
    assert t["channel"] == "whatsapp"
    assert t["contact_handle"] == "+34612345678"
    assert t["contact_name"] == "Bob"


@pytest.mark.asyncio
async def test_list_threads_channel_filter(inbox_client_with_thread) -> None:
    """GET /api/inbox/threads?channel=email returns empty when only whatsapp exists."""
    client, _thread, _cfg = inbox_client_with_thread
    r = client.get("/api/inbox/threads?channel=email")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["threads"] == []


@pytest.mark.asyncio
async def test_list_threads_channel_filter_match(inbox_client_with_thread) -> None:
    """GET /api/inbox/threads?channel=whatsapp returns the one whatsapp thread."""
    client, _thread, _cfg = inbox_client_with_thread
    r = client.get("/api/inbox/threads?channel=whatsapp")
    assert r.status_code == 200
    assert r.json()["count"] == 1


# ── GET /api/inbox/threads/changes ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_changes_returns_correct_shape(inbox_client) -> None:
    """GET /threads/changes returns {threads, deleted, now}."""
    r = inbox_client.get("/api/inbox/threads/changes")
    assert r.status_code == 200
    body = r.json()
    assert "threads" in body
    assert "deleted" in body
    assert "now" in body
    assert isinstance(body["threads"], list)
    assert isinstance(body["deleted"], list)
    assert isinstance(body["now"], str)


@pytest.mark.asyncio
async def test_changes_includes_seeded_thread(inbox_client_with_thread) -> None:
    """GET /threads/changes with no since returns the seeded thread."""
    client, thread, _cfg = inbox_client_with_thread
    r = client.get("/api/inbox/threads/changes")
    assert r.status_code == 200
    body = r.json()
    ids = [t["id"] for t in body["threads"]]
    assert thread["id"] in ids


@pytest.mark.asyncio
async def test_changes_since_filters_old(inbox_client_with_thread) -> None:
    """GET /threads/changes?since=<future> returns empty threads list."""
    client, _thread, _cfg = inbox_client_with_thread
    r = client.get("/api/inbox/threads/changes?since=2099-01-01T00:00:00+00:00")
    assert r.status_code == 200
    body = r.json()
    assert body["threads"] == []
    assert body["deleted"] == []


# ── POST /api/inbox/threads/{id}/read ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_read_returns_success(inbox_client_with_thread) -> None:
    """POST /threads/{id}/read zeros the unread count and returns {success: True}."""
    client, thread, _cfg = inbox_client_with_thread
    r = client.post(f"/api/inbox/threads/{thread['id']}/read")
    assert r.status_code == 200
    assert r.json()["success"] is True


@pytest.mark.asyncio
async def test_mark_read_zeros_unread(inbox_client_with_thread) -> None:
    """After mark-read, GET /threads returns unread_count=0."""
    client, thread, _cfg = inbox_client_with_thread
    client.post(f"/api/inbox/threads/{thread['id']}/read")
    r = client.get("/api/inbox/threads")
    t = r.json()["threads"][0]
    assert t["unread_count"] == 0


@pytest.mark.asyncio
async def test_mark_read_unknown_thread_returns_success_false(inbox_client) -> None:
    """POST /threads/nonexistent/read returns {success: False}."""
    r = inbox_client.post("/api/inbox/threads/nonexistent-id/read")
    assert r.status_code == 200
    assert r.json()["success"] is False


# ── GET /api/inbox/threads/{id}/messages ───────────────────────────────────────


@pytest.mark.asyncio
async def test_messages_404_for_unknown_thread(inbox_client) -> None:
    """GET /threads/bad-id/messages returns 404."""
    r = inbox_client.get("/api/inbox/threads/no-such-id/messages")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_messages_returns_live_messages(inbox_client_with_thread) -> None:
    """GET /threads/{id}/messages calls the gateway and returns messages + thread."""
    client, thread, cfg = inbox_client_with_thread
    fake_msgs = [
        Msg(sender="Bob", text="Hello", timestamp="2026-01-01T10:00:00", is_mine=False),
        Msg(sender="alice", text="Hi!", timestamp="2026-01-01T10:01:00", is_mine=True),
    ]
    fake_gw = MagicMock()
    fake_gw.read_thread = AsyncMock(return_value=fake_msgs)

    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=fake_gw):
        r = client.get(f"/api/inbox/threads/{thread['id']}/messages")

    assert r.status_code == 200
    body = r.json()
    assert "messages" in body
    assert "thread" in body
    assert len(body["messages"]) == 2
    assert body["messages"][0]["sender"] == "Bob"
    assert body["messages"][0]["text"] == "Hello"
    assert body["messages"][0]["is_mine"] is False
    assert body["thread"]["id"] == thread["id"]


# ── POST /api/inbox/threads/{id}/reply ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_reply_direct_returns_success(inbox_client_with_thread) -> None:
    """POST /threads/{id}/reply mode=direct calls send and returns {success: True, mode: 'direct'}."""
    client, thread, _cfg = inbox_client_with_thread
    fake_gw = MagicMock()
    fake_gw.send = AsyncMock(return_value=SendResult(ok=True))

    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=fake_gw):
        r = client.post(
            f"/api/inbox/threads/{thread['id']}/reply",
            json={"text": "Hey Bob!", "mode": "direct"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["mode"] == "direct"
    fake_gw.send.assert_awaited_once_with("whatsapp", "+34612345678", "Hey Bob!")


@pytest.mark.asyncio
async def test_reply_direct_default_mode(inbox_client_with_thread) -> None:
    """POST /threads/{id}/reply without explicit mode defaults to direct."""
    client, thread, _cfg = inbox_client_with_thread
    fake_gw = MagicMock()
    fake_gw.send = AsyncMock(return_value=SendResult(ok=True))

    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=fake_gw):
        r = client.post(
            f"/api/inbox/threads/{thread['id']}/reply",
            json={"text": "Default mode test"},
        )

    assert r.status_code == 200
    assert r.json()["mode"] == "direct"


@pytest.mark.asyncio
async def test_reply_direct_send_failure_raises_502(inbox_client_with_thread) -> None:
    """When gateway.send returns ok=False, reply returns HTTP 502."""
    client, thread, _cfg = inbox_client_with_thread
    fake_gw = MagicMock()
    fake_gw.send = AsyncMock(return_value=SendResult(ok=False, error="blocked by channel"))

    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=fake_gw):
        r = client.post(
            f"/api/inbox/threads/{thread['id']}/reply",
            json={"text": "This will fail", "mode": "direct"},
        )

    assert r.status_code == 502
    assert "blocked by channel" in r.json()["detail"]


@pytest.mark.asyncio
async def test_reply_404_for_unknown_thread(inbox_client) -> None:
    """POST /threads/bad-id/reply returns 404."""
    r = inbox_client.post(
        "/api/inbox/threads/no-such-id/reply",
        json={"text": "hello"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reply_ai_mode_lazy_import_missing_module(inbox_client_with_thread) -> None:
    """mode=ai returns 200 with mode='ai' when conversation_runner is available,
    or 503 when the module doesn't exist yet (Phase E not shipped).

    This test verifies the lazy-import guard: the route file itself must import
    cleanly even when conversation_runner doesn't exist.
    """
    client, thread, _cfg = inbox_client_with_thread
    # The module does not exist yet — the route must handle ImportError gracefully.
    r = client.post(
        f"/api/inbox/threads/{thread['id']}/reply",
        json={"text": "Start AI convo", "mode": "ai"},
    )
    # Either 503 (module missing) or 200 (module present) — never 500.
    assert r.status_code in (200, 503)
    assert r.status_code != 500


@pytest.mark.asyncio
async def test_reply_ai_mode_success(inbox_client_with_thread) -> None:
    """mode=ai with a mocked conversation_runner returns {success, conversation_id, mode='ai'}."""
    client, thread, _cfg = inbox_client_with_thread

    fake_start = AsyncMock(return_value={"id": "conv-123"})

    # Patch the start function on the real module (robust when module is already cached).
    from lazyclaw.comms import conversation_runner as _real_cr
    with patch.object(_real_cr, "start", fake_start):
        r = client.post(
            f"/api/inbox/threads/{thread['id']}/reply",
            json={"text": "Respond for me", "mode": "ai"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["mode"] == "ai"
    assert body["conversation_id"] == "conv-123"
