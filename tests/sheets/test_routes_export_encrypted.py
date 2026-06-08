"""POST /api/sheets/{id}/export — optional AES-256 password-encrypted zip."""

from __future__ import annotations

import io
from pathlib import Path

import pyzipper
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.sheets import router as sheets_router

_ZIP_MEDIA = "application/zip"
_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
    try:
        yield TestClient(app)
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_export_with_password_returns_encrypted_zip(client):
    sid = client.post("/api/sheets", json={"name": "Budget"}).json()["sheet"]["id"]

    r = client.post(
        f"/api/sheets/{sid}/export",
        json={"format": "xlsx", "password": "s3cret"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(_ZIP_MEDIA)
    assert 'filename="Budget.zip"' in r.headers["content-disposition"]

    # The zip needs the password and contains the real .xlsx.
    with pyzipper.AESZipFile(io.BytesIO(r.content)) as zf:
        zf.setpassword(b"s3cret")
        names = zf.namelist()
        assert names == ["Budget.xlsx"]
        inner = zf.read("Budget.xlsx")
        assert inner[:2] == b"PK"  # xlsx is itself a zip


@pytest.mark.asyncio
async def test_export_wrong_password_cannot_open(client):
    sid = client.post("/api/sheets", json={"name": "B"}).json()["sheet"]["id"]
    r = client.post(f"/api/sheets/{sid}/export", json={"format": "csv", "password": "right"})
    with pyzipper.AESZipFile(io.BytesIO(r.content)) as zf:
        zf.setpassword(b"wrong")
        with pytest.raises(Exception):
            zf.read(zf.namelist()[0])


@pytest.mark.asyncio
async def test_export_without_password_is_plain_xlsx(client):
    sid = client.post("/api/sheets", json={"name": "Plain"}).json()["sheet"]["id"]
    r = client.post(f"/api/sheets/{sid}/export", json={"format": "xlsx"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(_XLSX_MEDIA)
    assert 'filename="Plain.xlsx"' in r.headers["content-disposition"]
