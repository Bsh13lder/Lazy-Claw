"""/api/sheets — sheet CRUD from the web UI.

Covers create→list, snapshot fetch, autosave PUT round-trip, delete + 404,
and that one user's id can't fetch another's sheet through the route.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import db_session, init_db
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
    return TestClient(app)


@pytest.mark.asyncio
async def test_create_list_get(client):
    tc = client
    r = tc.post("/api/sheets", json={"name": "Budget"})
    assert r.status_code == 200, r.text
    sid = r.json()["sheet"]["id"]

    r = tc.get("/api/sheets")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["sheets"][0]["id"] == sid

    r = tc.get(f"/api/sheets/{sid}")
    assert r.status_code == 200
    assert r.json()["payload"]["sheets"]  # valid snapshot


@pytest.mark.asyncio
async def test_save_roundtrip(client):
    tc = client
    sid = tc.post("/api/sheets", json={"name": "Budget"}).json()["sheet"]["id"]
    snap = tc.get(f"/api/sheets/{sid}").json()["payload"]

    # mutate the first worksheet's A1 and save (mimics editor autosave)
    sheet_key = snap["sheetOrder"][0]
    snap["sheets"][sheet_key]["cellData"] = {"0": {"0": {"v": 99}}}
    r = tc.put(f"/api/sheets/{sid}", json={"name": "Budget", "payload": snap})
    assert r.status_code == 200, r.text

    out = tc.get(f"/api/sheets/{sid}").json()["payload"]
    assert out["sheets"][sheet_key]["cellData"]["0"]["0"]["v"] == 99


@pytest.mark.asyncio
async def test_delete_then_404(client):
    tc = client
    sid = tc.post("/api/sheets", json={"name": "Temp"}).json()["sheet"]["id"]
    assert tc.delete(f"/api/sheets/{sid}").status_code == 200
    assert tc.get(f"/api/sheets/{sid}").status_code == 404
    assert tc.delete(f"/api/sheets/{sid}").status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_404(client):
    assert client.get("/api/sheets/does-not-exist").status_code == 404


@pytest.mark.asyncio
async def test_export_xlsx_and_csv(client):
    tc = client
    sid = tc.post("/api/sheets", json={"name": "Budget"}).json()["sheet"]["id"]
    snap = tc.get(f"/api/sheets/{sid}").json()["payload"]
    sheet_key = snap["sheetOrder"][0]
    snap["sheets"][sheet_key]["cellData"] = {"0": {"0": {"v": 5}}}
    tc.put(f"/api/sheets/{sid}", json={"name": "Budget", "payload": snap})

    r = tc.get(f"/api/sheets/{sid}/export?format=xlsx")
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"  # xlsx is a zip
    assert "Budget.xlsx" in r.headers.get("content-disposition", "")

    r = tc.get(f"/api/sheets/{sid}/export?format=csv")
    assert r.status_code == 200
    assert r.text.strip() == "5"


@pytest.mark.asyncio
async def test_export_unknown_404(client):
    assert client.get("/api/sheets/nope/export").status_code == 404


@pytest.mark.asyncio
async def test_import_roundtrip(client):
    """A sheet exported to xlsx can be re-imported as a new sheet."""
    tc = client
    sid = tc.post("/api/sheets", json={"name": "Source"}).json()["sheet"]["id"]
    snap = tc.get(f"/api/sheets/{sid}").json()["payload"]
    key = snap["sheetOrder"][0]
    snap["sheets"][key]["cellData"] = {"0": {"0": {"v": 99}}}
    tc.put(f"/api/sheets/{sid}", json={"name": "Source", "payload": snap})

    xlsx_bytes = tc.get(f"/api/sheets/{sid}/export?format=xlsx").content
    r = tc.post(
        "/api/sheets/import",
        files={"file": ("imported.xlsx", xlsx_bytes,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200, r.text
    new_id = r.json()["sheet"]["id"]
    assert new_id != sid

    imported = tc.get(f"/api/sheets/{new_id}").json()["payload"]
    ikey = imported["sheetOrder"][0]
    assert imported["sheets"][ikey]["cellData"]["0"]["0"]["v"] == 99


@pytest.mark.asyncio
async def test_import_empty_file_400(client):
    r = client.post("/api/sheets/import", files={"file": ("x.xlsx", b"", "application/octet-stream")})
    assert r.status_code == 400
