"""Routes for per-channel standing instructions (Phase 5).

  GET    /api/inbox/channels/{channel}/instruction
  PUT    /api/inbox/channels/{channel}/instruction  {instruction}
  DELETE /api/inbox/channels/{channel}/instruction

Same fixture pattern as test_inbox_routes.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config, load_config
from lazyclaw.db.connection import close_pool, db_session, init_db
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


@pytest.fixture
async def client(tmp_path: Path):
    cfg = await _setup_db(tmp_path)
    try:
        yield TestClient(_make_app(cfg))
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_get_unset_instruction_returns_null(client) -> None:
    r = client.get("/api/inbox/channels/whatsapp/instruction")
    assert r.status_code == 200
    assert r.json()["instruction"] is None


@pytest.mark.asyncio
async def test_put_then_get_round_trip(client) -> None:
    r = client.put(
        "/api/inbox/channels/whatsapp/instruction",
        json={"instruction": "summarize and flag anything urgent"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["created"] is True
    assert body["job_id"]

    r2 = client.get("/api/inbox/channels/whatsapp/instruction")
    assert r2.status_code == 200
    assert r2.json()["instruction"] == "summarize and flag anything urgent"


@pytest.mark.asyncio
async def test_put_update_is_not_created(client) -> None:
    client.put(
        "/api/inbox/channels/whatsapp/instruction",
        json={"instruction": "first"},
    )
    r = client.put(
        "/api/inbox/channels/whatsapp/instruction",
        json={"instruction": "second"},
    )
    assert r.status_code == 200
    assert r.json()["created"] is False
    assert client.get(
        "/api/inbox/channels/whatsapp/instruction"
    ).json()["instruction"] == "second"


@pytest.mark.asyncio
async def test_delete_clears_instruction(client) -> None:
    client.put(
        "/api/inbox/channels/email/instruction",
        json={"instruction": "auto-ack invoices"},
    )
    r = client.delete("/api/inbox/channels/email/instruction")
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert client.get(
        "/api/inbox/channels/email/instruction"
    ).json()["instruction"] is None


@pytest.mark.asyncio
async def test_delete_when_unset_returns_false(client) -> None:
    r = client.delete("/api/inbox/channels/whatsapp/instruction")
    assert r.status_code == 200
    assert r.json()["success"] is False


@pytest.mark.asyncio
async def test_unsupported_channel_400(client) -> None:
    r = client.put(
        "/api/inbox/channels/telegram/instruction",
        json={"instruction": "x"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_invalid_channel_token_400(client) -> None:
    # Encoded-slash traversal is rejected by the router itself (404);
    # an uppercase token matches the route but fails _CHANNEL_PARAM_RE → 400.
    r = client.get("/api/inbox/channels/WH%2F..%2Fetc/instruction")
    assert r.status_code in (400, 404)
    r2 = client.get("/api/inbox/channels/WHATSAPP/instruction")
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_empty_instruction_rejected_by_schema(client) -> None:
    r = client.put(
        "/api/inbox/channels/whatsapp/instruction",
        json={"instruction": ""},
    )
    assert r.status_code == 422
