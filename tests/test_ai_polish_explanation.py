"""Manual 'polish my explanation' mode for tasks.

The user types their OWN rough explanation; the worker LLM rewrites it into
clean prose and it REPLACES the task description (no _AI:_ append). If the
worker is unavailable, the route must save the user's RAW text — never lose
their input. Empty input is rejected at the boundary (422).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.tasks.ai_polish_explanation import polish_explanation

pytestmark = pytest.mark.asyncio


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.content = content


# ── Module unit tests ───────────────────────────────────────────────────────


async def test_polish_returns_clean_text():
    with patch("lazyclaw.llm.eco_router.EcoRouter") as MockEco, \
         patch("lazyclaw.llm.router.LLMRouter"):
        MockEco.return_value.chat = AsyncMock(
            return_value=_FakeResp("Set up the venue and confirm the catering by Friday.")
        )
        out = await polish_explanation(
            None, "u1",
            raw_text="set up venu, also catering frieday",
            title="Club opening", category="ClubBay",
        )
    assert out == "Set up the venue and confirm the catering by Friday."


async def test_polish_empty_input_returns_none():
    out = await polish_explanation(
        None, "u1", raw_text="   ", title="x", category=None,
    )
    assert out is None


async def test_polish_llm_failure_returns_none():
    with patch("lazyclaw.llm.eco_router.EcoRouter") as MockEco, \
         patch("lazyclaw.llm.router.LLMRouter"):
        MockEco.return_value.chat = AsyncMock(side_effect=RuntimeError("worker down"))
        out = await polish_explanation(
            None, "u1", raw_text="some rough text", title="x", category=None,
        )
    assert out is None


# ── Route tests ───────────────────────────────────────────────────────────


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    cfg = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(cfg, "u1", "salt-a")

    import lazyclaw.gateway.routes.tasks as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(routes_mod.router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    try:
        yield (TestClient(app), cfg)
    finally:
        await close_pool()


async def _make_task(cfg) -> str:
    from lazyclaw.tasks.store import create_task
    task = await create_task(cfg, "u1", "Club opening", category="ClubBay")
    return task["id"]


async def test_route_polishes_and_replaces_description(client):
    tc, cfg = client
    tid = await _make_task(cfg)

    with patch(
        "lazyclaw.tasks.ai_polish_explanation.polish_explanation",
        new=AsyncMock(return_value="Polished prose."),
    ):
        r = tc.post(
            f"/api/tasks/{tid}/ai-polish-explanation",
            json={"explanation_text": "rough words"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_text"] == "Polished prose."
    # Replaces, does NOT append under an _AI:_ divider.
    assert body["task"]["description"] == "Polished prose."
    assert "_AI:_" not in (body["task"]["description"] or "")


async def test_route_saves_raw_text_when_worker_down(client):
    tc, cfg = client
    tid = await _make_task(cfg)

    with patch(
        "lazyclaw.tasks.ai_polish_explanation.polish_explanation",
        new=AsyncMock(return_value=None),  # worker unavailable
    ):
        r = tc.post(
            f"/api/tasks/{tid}/ai-polish-explanation",
            json={"explanation_text": "my own raw explanation"},
        )
    assert r.status_code == 200, r.text
    # Raw text saved verbatim — input never lost.
    assert r.json()["task"]["description"] == "my own raw explanation"


async def test_route_rejects_empty_text(client):
    tc, cfg = client
    tid = await _make_task(cfg)
    r = tc.post(
        f"/api/tasks/{tid}/ai-polish-explanation",
        json={"explanation_text": ""},
    )
    assert r.status_code == 422
