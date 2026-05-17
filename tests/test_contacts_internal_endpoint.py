"""Tests for the /api/internal/contacts/search bridge endpoint.

This endpoint is what mcp-whatsapp calls to look up macOS-synced contacts
before sending a WhatsApp message. The bug being prevented: WhatsApp MCP
returning "Contact not found" for someone the user has clearly saved in
their phonebook, leading to silent guess-the-number failures.

Coverage:
- 401 when X-Internal-Token is missing
- 401 when X-Internal-Token is wrong
- 200 with valid token returns matches in the documented shape
- 400 when required params are missing
- Empty matches when query has no hits
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.contacts.store import create_contact
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.internal_auth import get_internal_token
from lazyclaw.gateway.routes.contacts import router as internal_contacts_router


@pytest.fixture
async def tmp_config_with_contact(tmp_path: Path, monkeypatch):
    cfg = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    # Provision DEK so encryption works during create_contact.
    await create_user_dek(cfg, "u1", "salt-a")

    # Make ``load_config()`` inside the route return *this* config.
    import lazyclaw.gateway.routes.contacts as routes_mod
    monkeypatch.setattr(routes_mod, "load_config", lambda: cfg)

    await create_contact(
        cfg, "u1",
        name="Buchvardi",
        aliases=["Bichi"],
        handles={"phone": ["+34641952564"]},
        source="macos_contacts",
    )

    try:
        yield cfg
    finally:
        await close_pool()


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(internal_contacts_router)
    return app


@pytest.mark.asyncio
async def test_endpoint_requires_token(tmp_config_with_contact):
    client = TestClient(_make_app())
    # No header
    r = client.get("/api/internal/contacts/search", params={"user_id": "u1", "query": "Buchvardi"})
    assert r.status_code == 401
    # Wrong header
    r = client.get(
        "/api/internal/contacts/search",
        params={"user_id": "u1", "query": "Buchvardi"},
        headers={"X-Internal-Token": "nope"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_returns_macos_contact(tmp_config_with_contact):
    client = TestClient(_make_app())
    r = client.get(
        "/api/internal/contacts/search",
        params={"user_id": "u1", "query": "Buchvardi"},
        headers={"X-Internal-Token": get_internal_token()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "matches" in body
    assert len(body["matches"]) == 1
    match = body["matches"][0]
    assert match["name"] == "Buchvardi"
    assert match["handles"]["phone"] == ["+34641952564"]
    # Critical: caller (mcp-whatsapp) reads this exact shape — keep stable.
    assert "Bichi" in match["aliases"]


@pytest.mark.asyncio
async def test_endpoint_matches_by_alias(tmp_config_with_contact):
    client = TestClient(_make_app())
    r = client.get(
        "/api/internal/contacts/search",
        params={"user_id": "u1", "query": "Bichi"},
        headers={"X-Internal-Token": get_internal_token()},
    )
    assert r.status_code == 200
    matches = r.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["name"] == "Buchvardi"


@pytest.mark.asyncio
async def test_endpoint_returns_empty_on_miss(tmp_config_with_contact):
    client = TestClient(_make_app())
    r = client.get(
        "/api/internal/contacts/search",
        params={"user_id": "u1", "query": "Zzzzz_no_such_person"},
        headers={"X-Internal-Token": get_internal_token()},
    )
    assert r.status_code == 200
    assert r.json()["matches"] == []


@pytest.mark.asyncio
async def test_endpoint_rejects_empty_query(tmp_config_with_contact):
    client = TestClient(_make_app())
    r = client.get(
        "/api/internal/contacts/search",
        params={"user_id": "u1", "query": ""},
        headers={"X-Internal-Token": get_internal_token()},
    )
    # FastAPI validates min_length=1 → 422
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_endpoint_isolates_users(tmp_config_with_contact):
    """A query under user u2 must NOT see u1's contacts."""
    cfg = tmp_config_with_contact
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u2", "bob", "x", "salt-b"),
        )
        await db.commit()
    await create_user_dek(cfg, "u2", "salt-b")

    client = TestClient(_make_app())
    r = client.get(
        "/api/internal/contacts/search",
        params={"user_id": "u2", "query": "Buchvardi"},
        headers={"X-Internal-Token": get_internal_token()},
    )
    assert r.status_code == 200
    assert r.json()["matches"] == []
