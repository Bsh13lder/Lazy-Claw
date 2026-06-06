"""Tests for mobile-friendly GET/POST /api/settings/eco
and confirmation of existing permissions GET/PATCH shapes.

Covers:
  - GET /api/settings/eco  → compact {mode, modes, brain, worker, fallback}
  - POST /api/settings/eco → validates + persists + returns compact shape
  - POST /api/settings/eco → rejects invalid mode
  - GET /api/permissions/settings → {category_defaults, skill_overrides, ...}
  - PATCH /api/permissions/settings → set category_defaults
  - PATCH /api/permissions/skills/{name} → set skill override
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.mobile_settings import router as mobile_settings_router
from lazyclaw.gateway.routes.permissions import router as permissions_router


# ── Shared fixtures ────────────────────────────────────────────────────────────


async def _setup_db(tmp_path: Path) -> Config:
    """Create a minimal in-memory-style DB with one user."""
    cfg = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    return cfg


def _fake_user() -> User:
    return User(id="u1", username="alice", display_name=None,
                encryption_salt="salt-a", role="user")


# ── ECO endpoint fixtures ──────────────────────────────────────────────────────


@pytest.fixture
async def eco_client(tmp_path: Path):
    cfg = await _setup_db(tmp_path)

    import lazyclaw.gateway.routes.mobile_settings as mod
    # load_config is injected via Depends — override it to use our test cfg
    from lazyclaw.config import load_config

    app = FastAPI()
    app.include_router(mobile_settings_router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[load_config] = lambda: cfg

    return TestClient(app)


# ── Permissions endpoint fixtures ──────────────────────────────────────────────


@pytest.fixture
async def perms_client(tmp_path: Path):
    cfg = await _setup_db(tmp_path)

    from lazyclaw.config import load_config

    app = FastAPI()
    app.include_router(permissions_router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[load_config] = lambda: cfg

    return TestClient(app)


# ── ECO GET tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eco_get_returns_compact_shape(eco_client) -> None:
    """GET /api/settings/eco returns {mode, modes, brain, worker, fallback}."""
    r = eco_client.get("/api/settings/eco")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert set(data.keys()) >= {"mode", "modes", "brain", "worker", "fallback"}


@pytest.mark.asyncio
async def test_eco_get_default_mode_is_hybrid(eco_client) -> None:
    """Fresh user defaults to HYBRID mode."""
    r = eco_client.get("/api/settings/eco")
    data = r.json()["data"]
    assert data["mode"] == "HYBRID"


@pytest.mark.asyncio
async def test_eco_get_modes_list(eco_client) -> None:
    """modes list contains all four valid modes in expected order."""
    r = eco_client.get("/api/settings/eco")
    modes = r.json()["data"]["modes"]
    assert modes == ["HYBRID", "FULL", "CLAUDE", "MINIMAX"]


@pytest.mark.asyncio
async def test_eco_get_brain_worker_fallback_populated(eco_client) -> None:
    """brain, worker, fallback are non-empty strings."""
    r = eco_client.get("/api/settings/eco")
    data = r.json()["data"]
    assert isinstance(data["brain"], str) and data["brain"]
    assert isinstance(data["worker"], str) and data["worker"]
    assert isinstance(data["fallback"], str) and data["fallback"]


# ── ECO POST tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eco_post_valid_mode_persists(eco_client) -> None:
    """POST {mode: 'FULL'} sets the mode and returns compact shape."""
    r = eco_client.post("/api/settings/eco", json={"mode": "FULL"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["mode"] == "FULL"
    assert "modes" in data
    assert "brain" in data
    assert "worker" in data
    assert "fallback" in data


@pytest.mark.asyncio
async def test_eco_post_persists_across_get(eco_client) -> None:
    """POST then GET returns the updated mode."""
    eco_client.post("/api/settings/eco", json={"mode": "MINIMAX"})
    r = eco_client.get("/api/settings/eco")
    assert r.json()["data"]["mode"] == "MINIMAX"


@pytest.mark.asyncio
async def test_eco_post_case_insensitive(eco_client) -> None:
    """Mode input is case-insensitive: 'claude' == 'CLAUDE'."""
    r = eco_client.post("/api/settings/eco", json={"mode": "claude"})
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert r.json()["data"]["mode"] == "CLAUDE"


@pytest.mark.asyncio
async def test_eco_post_invalid_mode_rejected(eco_client) -> None:
    """POST with an unknown mode returns success=false + error, not 4xx."""
    r = eco_client.post("/api/settings/eco", json={"mode": "TURBO"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "error" in body
    assert "TURBO" in body["error"] or "Invalid" in body["error"]


@pytest.mark.asyncio
async def test_eco_post_disabled_mode_rejected(eco_client) -> None:
    """Disabled legacy modes (local/eco) are also rejected."""
    r = eco_client.post("/api/settings/eco", json={"mode": "local"})
    assert r.status_code == 200
    assert r.json()["success"] is False


@pytest.mark.asyncio
async def test_eco_post_all_valid_modes(eco_client) -> None:
    """All four valid modes can be set via POST."""
    for mode in ["HYBRID", "FULL", "CLAUDE", "MINIMAX"]:
        r = eco_client.post("/api/settings/eco", json={"mode": mode})
        assert r.status_code == 200, f"mode={mode} → {r.text}"
        body = r.json()
        assert body["success"] is True, f"mode={mode} failed: {body}"
        assert body["data"]["mode"] == mode


# ── Permissions GET tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_get_returns_shape(perms_client) -> None:
    """GET /api/permissions/settings returns {category_defaults, skill_overrides, ...}."""
    r = perms_client.get("/api/permissions/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert "category_defaults" in data
    assert "skill_overrides" in data
    assert "auto_approve_timeout" in data
    assert "require_approval_for_heartbeat" in data


@pytest.mark.asyncio
async def test_permissions_get_category_defaults(perms_client) -> None:
    """category_defaults contains known categories with allow/ask/deny values."""
    r = perms_client.get("/api/permissions/settings")
    cats = r.json()["data"]["category_defaults"]
    # Spot-check defaults from DEFAULT_CATEGORY_PERMISSIONS
    assert cats.get("core") == "allow"
    assert cats.get("vault") == "ask"
    assert cats.get("computer") == "ask"
    assert cats.get("tasks") == "allow"


@pytest.mark.asyncio
async def test_permissions_get_skill_overrides_empty_by_default(perms_client) -> None:
    """Fresh user has no skill overrides."""
    r = perms_client.get("/api/permissions/settings")
    assert r.json()["data"]["skill_overrides"] == {}


# ── Permissions PATCH /settings tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_patch_category_default(perms_client) -> None:
    """PATCH category_defaults changes a category permission."""
    r = perms_client.patch(
        "/api/permissions/settings",
        json={"category_defaults": {"vault": "allow"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["category_defaults"]["vault"] == "allow"


@pytest.mark.asyncio
async def test_permissions_patch_persists(perms_client) -> None:
    """PATCH then GET reflects the update."""
    perms_client.patch(
        "/api/permissions/settings",
        json={"category_defaults": {"computer": "allow"}},
    )
    r = perms_client.get("/api/permissions/settings")
    assert r.json()["data"]["category_defaults"]["computer"] == "allow"


@pytest.mark.asyncio
async def test_permissions_patch_invalid_level_rejected(perms_client) -> None:
    """PATCH with invalid level returns error."""
    r = perms_client.patch(
        "/api/permissions/settings",
        json={"category_defaults": {"vault": "maybe"}},
    )
    assert r.status_code == 200
    assert r.json()["success"] is False


# ── Permissions PATCH /skills/{name} tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_skill_override_set(perms_client) -> None:
    """PATCH /api/permissions/skills/{name} sets an override."""
    r = perms_client.patch(
        "/api/permissions/skills/my_custom_skill",
        json={"level": "deny"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["skill"] == "my_custom_skill"
    assert body["data"]["level"] == "deny"


@pytest.mark.asyncio
async def test_permissions_skill_override_invalid_level(perms_client) -> None:
    """PATCH /api/permissions/skills/{name} rejects invalid level."""
    r = perms_client.patch(
        "/api/permissions/skills/my_custom_skill",
        json={"level": "SUPERALLOW"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_permissions_skill_override_persisted(perms_client) -> None:
    """Setting a skill override is reflected in GET /settings."""
    perms_client.patch(
        "/api/permissions/skills/browser",
        json={"level": "ask"},
    )
    r = perms_client.get("/api/permissions/settings")
    overrides = r.json()["data"]["skill_overrides"]
    assert overrides.get("browser") == "ask"
