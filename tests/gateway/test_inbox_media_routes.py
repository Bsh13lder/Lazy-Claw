"""Tests for inbox media surfacing + the media download endpoint.

  - GET /api/inbox/threads/{id}/messages now carries ``id`` + ``media``
  - GET /api/inbox/threads/{id}/media/{message_id} streams downloaded bytes

Same fixture pattern as test_inbox_routes.py (dependency_overrides + patched
build_gateway).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config, load_config
from lazyclaw.comms import thread_store
from lazyclaw.comms.models import Msg, ReadResult
from lazyclaw.db.connection import db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.inbox import router as inbox_router


async def _setup_db(tmp_path: Path) -> Config:
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


@pytest.fixture(autouse=True)
def _reset_media_limiter():
    from lazyclaw.gateway.routes import inbox as inbox_mod
    inbox_mod._media_limiter._requests.clear()
    yield
    inbox_mod._media_limiter._requests.clear()


@pytest.fixture
async def client_with_thread(tmp_path: Path):
    cfg = await _setup_db(tmp_path)
    thread = await thread_store.upsert_thread(
        cfg, "u1",
        channel="whatsapp",
        contact_handle="+34612345678",
        contact_name="Maria",
        preview="[audio]",
        increment_unread=True,
    )
    return TestClient(_make_app(cfg)), thread, cfg


# ── messages carry media metadata ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_messages_include_id_and_media(client_with_thread) -> None:
    client, thread, _cfg = client_with_thread
    voice = Msg(
        sender="Maria", text="[audio]", timestamp="10:00", is_mine=False,
        id="MSG1",
        media={"kind": "audio", "mimetype": "audio/ogg; codecs=opus",
               "voice_note": True, "seconds": 14,
               "file_name": None, "size_bytes": 9001},
    )
    gw = MagicMock()
    gw.read_thread = AsyncMock(return_value=ReadResult(ok=True, messages=(voice,)))
    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=gw):
        r = client.get(f"/api/inbox/threads/{thread['id']}/messages")
    assert r.status_code == 200
    m = r.json()["messages"][0]
    assert m["id"] == "MSG1"
    assert m["media"]["kind"] == "audio"
    assert m["media"]["voice_note"] is True


@pytest.mark.asyncio
async def test_text_messages_have_null_media(client_with_thread) -> None:
    client, thread, _cfg = client_with_thread
    gw = MagicMock()
    gw.read_thread = AsyncMock(return_value=ReadResult(
        ok=True, messages=(Msg(sender="Maria", text="hola", timestamp="10:01"),),
    ))
    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=gw):
        r = client.get(f"/api/inbox/threads/{thread['id']}/messages")
    m = r.json()["messages"][0]
    assert m["media"] is None
    assert m["id"] is None


# ── GET media endpoint ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_media_download_streams_file(client_with_thread, tmp_path) -> None:
    client, thread, _cfg = client_with_thread
    blob = tmp_path / "MSG1_voice.ogg"
    blob.write_bytes(b"OggS-fake-voice-bytes")
    gw = MagicMock()
    gw.download_media = AsyncMock(return_value={
        "path": str(blob), "kind": "audio",
        "mimetype": "audio/ogg; codecs=opus", "voice_note": True,
    })
    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=gw):
        r = client.get(f"/api/inbox/threads/{thread['id']}/media/MSG1")
    assert r.status_code == 200
    assert r.content == b"OggS-fake-voice-bytes"
    assert r.headers["content-type"].startswith("audio/ogg")
    gw.download_media.assert_awaited_once_with(
        "whatsapp", "MSG1", contact="+34612345678",
    )


@pytest.mark.asyncio
async def test_media_download_invalid_message_id_rejected(client_with_thread) -> None:
    client, thread, _cfg = client_with_thread
    # Encoded slashes never reach the handler — Starlette rejects the
    # traversal probe at the router (404). Defense-in-depth only.
    r = client.get(f"/api/inbox/threads/{thread['id']}/media/..%2F..%2Fetc")
    assert r.status_code in (400, 404)
    # A token that DOES match the route but fails the id regex (space)
    # must be rejected by the handler's validation with 400.
    r2 = client.get(f"/api/inbox/threads/{thread['id']}/media/MSG%20ONE")
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_media_download_thread_not_found(client_with_thread) -> None:
    client, _thread, _cfg = client_with_thread
    r = client.get("/api/inbox/threads/nope/media/MSG1")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_media_download_tool_error_is_generic_502(client_with_thread) -> None:
    client, thread, _cfg = client_with_thread
    gw = MagicMock()
    gw.download_media = AsyncMock(return_value={
        "error": "Message 'MSG1' not found in /internal/secret/path",
    })
    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=gw):
        r = client.get(f"/api/inbox/threads/{thread['id']}/media/MSG1")
    assert r.status_code == 502
    # internal error details must NOT leak to the client
    assert "/internal/secret/path" not in r.text


@pytest.mark.asyncio
async def test_media_download_missing_file_502(client_with_thread) -> None:
    client, thread, _cfg = client_with_thread
    gw = MagicMock()
    gw.download_media = AsyncMock(return_value={
        "path": "/nonexistent/file.ogg", "mimetype": "audio/ogg",
    })
    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=gw):
        r = client.get(f"/api/inbox/threads/{thread['id']}/media/MSG1")
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_media_download_rate_limited(client_with_thread, tmp_path) -> None:
    client, thread, _cfg = client_with_thread
    blob = tmp_path / "m.jpg"
    blob.write_bytes(b"jpegbytes")
    gw = MagicMock()
    gw.download_media = AsyncMock(return_value={
        "path": str(blob), "mimetype": "image/jpeg",
    })
    from lazyclaw.gateway.routes import inbox as inbox_mod
    with patch("lazyclaw.gateway.routes.inbox.build_gateway", return_value=gw):
        cap = inbox_mod._media_limiter._max
        for _ in range(cap):
            assert client.get(
                f"/api/inbox/threads/{thread['id']}/media/MSG1"
            ).status_code == 200
        r = client.get(f"/api/inbox/threads/{thread['id']}/media/MSG1")
    assert r.status_code == 429
