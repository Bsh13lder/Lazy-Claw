"""/api/specialists — REST CRUD for built-in (read-only) + custom specialists.

The teams ``specialist`` CRUD layer is mocked at the route-module level (an
in-memory fake that honours the real contract: ``save``/``delete`` raise
``ValueError`` on built-in collisions). This isolates the router's job —
validation, the locked wire shape, the ``{"ok": ...}`` envelope, and the
ValueError→HTTP-400 mapping — from the encrypted DB layer.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.teams.specialist import SpecialistConfig

import lazyclaw.gateway.routes.specialists as routes_mod


# Two stand-in built-ins (read-only, is_builtin=True).
BUILTINS: tuple[SpecialistConfig, ...] = (
    SpecialistConfig(
        name="browser_specialist",
        display_name="Browser Specialist",
        system_prompt="Drive the browser.",
        allowed_skills=("browser",),
        preferred_model=None,
        is_builtin=True,
        include_scraper=True,
    ),
    SpecialistConfig(
        name="code_specialist",
        display_name="Code Specialist",
        system_prompt="Write code.",
        allowed_skills=("bash", "edit"),
        preferred_model=None,
        is_builtin=True,
    ),
)
_BUILTIN_NAMES = {b.name for b in BUILTINS}

EXPECTED_SHAPE_KEYS = {
    "name",
    "display_name",
    "system_prompt",
    "tools",
    "model",
    "include_scraper",
    "is_builtin",
}


@pytest.fixture
def client(monkeypatch) -> TestClient:
    # In-memory custom-specialist store keyed by name.
    store: dict[str, SpecialistConfig] = {}

    async def fake_load_specialists(config, user_id):
        return list(BUILTINS) + list(store.values())

    async def fake_get_specialist(config, user_id, name):
        for b in BUILTINS:
            if b.name == name:
                return b
        return store.get(name)

    async def fake_save_specialist(config, user_id, spec: SpecialistConfig):
        if spec.is_builtin:
            raise ValueError("Cannot save a built-in specialist")
        if spec.name in _BUILTIN_NAMES:
            raise ValueError(
                f"Name '{spec.name}' conflicts with a built-in specialist"
            )
        store[spec.name] = spec
        return f"rec-{spec.name}"

    async def fake_delete_specialist(config, user_id, name):
        if name in _BUILTIN_NAMES:
            raise ValueError("Cannot delete a built-in specialist")
        if name in store:
            del store[name]
            return True
        return False

    monkeypatch.setattr(routes_mod, "load_specialists", fake_load_specialists)
    monkeypatch.setattr(routes_mod, "get_specialist", fake_get_specialist)
    monkeypatch.setattr(routes_mod, "save_specialist", fake_save_specialist)
    monkeypatch.setattr(routes_mod, "delete_specialist", fake_delete_specialist)

    app = FastAPI()
    app.include_router(routes_mod.router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None, encryption_salt="s", role="user"
    )
    # CRUD is fully faked, so the resolved Config is never touched.
    app.dependency_overrides[routes_mod.load_config] = lambda: None
    return TestClient(app)


# ── list ──────────────────────────────────────────────────────────────


def test_list_includes_builtins_as_readonly(client: TestClient) -> None:
    r = client.get("/api/specialists")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True

    by_name = {s["name"]: s for s in body["specialists"]}
    assert "browser_specialist" in by_name
    assert "code_specialist" in by_name
    # Built-ins are read-only.
    assert by_name["browser_specialist"]["is_builtin"] is True
    assert by_name["code_specialist"]["is_builtin"] is True
    # Locked wire shape.
    assert set(by_name["browser_specialist"].keys()) == EXPECTED_SHAPE_KEYS
    assert by_name["browser_specialist"]["tools"] == ["browser"]
    assert by_name["browser_specialist"]["include_scraper"] is True
    assert by_name["browser_specialist"]["model"] is None


# ── create ────────────────────────────────────────────────────────────


def test_create_custom_specialist(client: TestClient) -> None:
    payload = {
        "name": "my_helper",
        "display_name": "My Helper",
        "system_prompt": "You help with chores.",
        "tools": ["web_search", "browser"],
        "model": "haiku",
        "include_scraper": True,
    }
    r = client.post("/api/specialists", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True

    spec = body["specialist"]
    assert set(spec.keys()) == EXPECTED_SHAPE_KEYS
    assert spec["name"] == "my_helper"
    assert spec["display_name"] == "My Helper"
    assert spec["tools"] == ["web_search", "browser"]
    assert spec["model"] == "haiku"
    assert spec["include_scraper"] is True
    assert spec["is_builtin"] is False

    # It is now listed alongside the built-ins.
    listed = {s["name"] for s in client.get("/api/specialists").json()["specialists"]}
    assert "my_helper" in listed


def test_create_defaults_model_and_scraper(client: TestClient) -> None:
    r = client.post(
        "/api/specialists",
        json={
            "name": "minimal",
            "display_name": "Minimal",
            "system_prompt": "Do one thing.",
            "tools": ["browser"],
        },
    )
    assert r.status_code == 200, r.text
    spec = r.json()["specialist"]
    assert spec["model"] is None
    assert spec["include_scraper"] is False


# ── update ────────────────────────────────────────────────────────────


def test_update_custom_specialist(client: TestClient) -> None:
    client.post(
        "/api/specialists",
        json={
            "name": "my_helper",
            "display_name": "My Helper",
            "system_prompt": "Old prompt.",
            "tools": ["browser"],
        },
    )
    r = client.put(
        "/api/specialists/my_helper",
        json={
            "display_name": "Renamed Helper",
            "system_prompt": "New prompt.",
            "tools": ["web_search"],
            "include_scraper": True,
        },
    )
    assert r.status_code == 200, r.text
    spec = r.json()["specialist"]
    assert spec["display_name"] == "Renamed Helper"
    assert spec["system_prompt"] == "New prompt."
    assert spec["tools"] == ["web_search"]
    assert spec["include_scraper"] is True
    assert spec["is_builtin"] is False


def test_update_partial_preserves_unspecified_fields(client: TestClient) -> None:
    client.post(
        "/api/specialists",
        json={
            "name": "keep_me",
            "display_name": "Keep Me",
            "system_prompt": "Original.",
            "tools": ["browser", "edit"],
            "model": "sonnet",
        },
    )
    r = client.put("/api/specialists/keep_me", json={"display_name": "Kept"})
    assert r.status_code == 200, r.text
    spec = r.json()["specialist"]
    assert spec["display_name"] == "Kept"
    # Unspecified fields are preserved.
    assert spec["system_prompt"] == "Original."
    assert spec["tools"] == ["browser", "edit"]
    assert spec["model"] == "sonnet"


def test_update_missing_specialist_returns_404(client: TestClient) -> None:
    r = client.put("/api/specialists/ghost", json={"display_name": "Boo"})
    assert r.status_code == 404
    assert r.json()["ok"] is False


# ── delete ────────────────────────────────────────────────────────────


def test_delete_custom_specialist(client: TestClient) -> None:
    client.post(
        "/api/specialists",
        json={
            "name": "trash",
            "display_name": "Trash",
            "system_prompt": "Temp.",
            "tools": ["browser"],
        },
    )
    r = client.delete("/api/specialists/trash")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "deleted": True}

    listed = {s["name"] for s in client.get("/api/specialists").json()["specialists"]}
    assert "trash" not in listed


def test_delete_missing_specialist_returns_404(client: TestClient) -> None:
    r = client.delete("/api/specialists/ghost")
    assert r.status_code == 404
    assert r.json()["ok"] is False


# ── built-in read-only enforcement ────────────────────────────────────


def test_update_builtin_returns_400(client: TestClient) -> None:
    r = client.put(
        "/api/specialists/browser_specialist",
        json={"display_name": "Hacked"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert "built-in" in body["error"].lower()


def test_delete_builtin_returns_400(client: TestClient) -> None:
    r = client.delete("/api/specialists/code_specialist")
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert "built-in" in body["error"].lower()


def test_create_with_builtin_name_returns_400(client: TestClient) -> None:
    r = client.post(
        "/api/specialists",
        json={
            "name": "browser_specialist",
            "display_name": "Clash",
            "system_prompt": "x",
            "tools": ["browser"],
        },
    )
    assert r.status_code == 400
    assert r.json()["ok"] is False


# ── input validation (HTTP 400) ───────────────────────────────────────


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"name": "Bad Name", "display_name": "X", "system_prompt": "p", "tools": ["t"]}, "name not a slug"),
        ({"name": "UPPER", "display_name": "X", "system_prompt": "p", "tools": ["t"]}, "uppercase name"),
        ({"name": "has-dash", "display_name": "X", "system_prompt": "p", "tools": ["t"]}, "dash in name"),
        ({"name": "", "display_name": "X", "system_prompt": "p", "tools": ["t"]}, "empty name"),
        ({"display_name": "X", "system_prompt": "p", "tools": ["t"]}, "missing name"),
        ({"name": "ok", "display_name": "", "system_prompt": "p", "tools": ["t"]}, "empty display_name"),
        ({"name": "ok", "display_name": "X", "system_prompt": "", "tools": ["t"]}, "empty system_prompt"),
        ({"name": "ok", "display_name": "X", "system_prompt": "   ", "tools": ["t"]}, "whitespace prompt"),
        ({"name": "ok", "display_name": "X", "system_prompt": "p", "tools": ["good", ""]}, "empty tool string"),
        ({"name": "ok", "display_name": "X", "system_prompt": "p", "tools": ["  "]}, "whitespace tool"),
    ],
)
def test_create_invalid_body_returns_400(client: TestClient, payload, reason) -> None:
    r = client.post("/api/specialists", json=payload)
    assert r.status_code == 400, f"{reason}: {r.text}"
    assert r.json()["ok"] is False


def test_update_invalid_body_returns_400(client: TestClient) -> None:
    client.post(
        "/api/specialists",
        json={
            "name": "editme",
            "display_name": "Edit Me",
            "system_prompt": "Original.",
            "tools": ["browser"],
        },
    )
    # Blanking system_prompt on update is rejected.
    r = client.put("/api/specialists/editme", json={"system_prompt": "   "})
    assert r.status_code == 400
    assert r.json()["ok"] is False

    # An empty-string tool on update is rejected.
    r = client.put("/api/specialists/editme", json={"tools": [""]})
    assert r.status_code == 400
    assert r.json()["ok"] is False


# ── auth ──────────────────────────────────────────────────────────────


def test_requires_authentication() -> None:
    app = FastAPI()
    app.include_router(routes_mod.router)
    app.dependency_overrides[routes_mod.load_config] = lambda: None
    tc = TestClient(app)
    r = tc.get("/api/specialists")
    assert r.status_code == 401
