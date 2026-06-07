"""/api/sheets/{id}/recalc — server-side formula recompute for native clients."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.sheets import router as sheets_router


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

    import lazyclaw.gateway.routes.sheets as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(sheets_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    # close_pool() in teardown: the shared aiosqlite pool keeps a non-daemon
    # worker thread that otherwise blocks interpreter exit (process hangs).
    try:
        yield TestClient(app)
    finally:
        await close_pool()


def _snap_with_sum():
    """A1=2, A2=3, A3==SUM(A1:A2) (value not yet computed)."""
    return {
        "id": "wb-1",
        "name": "Budget",
        "sheetOrder": ["s1"],
        "sheets": {
            "s1": {
                "id": "s1",
                "name": "Sheet1",
                "rowCount": 100,
                "columnCount": 26,
                "cellData": {
                    "0": {"0": {"v": 2}},
                    "1": {"0": {"v": 3}},
                    "2": {"0": {"f": "=SUM(A1:A2)"}},
                },
            }
        },
    }


@pytest.mark.asyncio
async def test_recalc_returns_computed_values(client):
    r = client.post("/api/sheets/wb-1/recalc", json={"payload": _snap_with_sum()})
    assert r.status_code == 200, r.text
    out = r.json()["snapshot"]
    assert out["sheets"]["s1"]["cellData"]["2"]["0"]["v"] == 5


@pytest.mark.asyncio
async def test_recalc_no_formulas_is_noop(client):
    snap = {
        "id": "wb-2", "name": "B", "sheetOrder": ["s1"],
        "sheets": {"s1": {"id": "s1", "name": "Sheet1", "cellData": {"0": {"0": {"v": 7}}}}},
    }
    r = client.post("/api/sheets/wb-2/recalc", json={"payload": snap})
    assert r.status_code == 200
    assert r.json()["snapshot"]["sheets"]["s1"]["cellData"]["0"]["0"]["v"] == 7


@pytest.mark.asyncio
async def test_recalc_bad_formula_never_500(client):
    snap = {
        "id": "wb-3", "name": "B", "sheetOrder": ["s1"],
        "sheets": {"s1": {"id": "s1", "name": "Sheet1",
                          "cellData": {"0": {"0": {"f": "=NOTAFUNC(1)"}}}}},
    }
    r = client.post("/api/sheets/wb-3/recalc", json={"payload": snap})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
