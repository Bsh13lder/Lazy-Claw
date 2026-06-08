"""/api/docs/import — upload a .docx to create a new document."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.docs import snapshot as D
from lazyclaw.docs.docx_io import snapshot_to_docx
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.docs import router as docs_router

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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

    import lazyclaw.gateway.routes.docs as routes_mod
    monkeypatch.setattr(routes_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(docs_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    try:
        yield TestClient(app)
    finally:
        await close_pool()


def _docx_with_list() -> bytes:
    snap = {
        "id": "doc-x",
        "documentStyle": {},
        "body": D.build_body_with_blocks([
            {"type": "heading", "level": 1, "runs": [{"text": "Imported"}]},
            {"type": "number", "level": 0, "runs": [{"text": "first"}]},
            {"type": "number", "level": 0, "runs": [{"text": "second"}]},
        ]),
    }
    return snapshot_to_docx(snap)


@pytest.mark.asyncio
async def test_import_docx_creates_doc_with_content(client):
    data = _docx_with_list()
    r = client.post(
        "/api/docs/import",
        files={"file": ("Report.docx", data, _DOCX_MEDIA)},
    )
    assert r.status_code == 200, r.text
    doc = r.json()["doc"]
    assert doc["name"] == "Report"

    # The imported doc is fetchable and round-trips the numbered list.
    got = client.get(f"/api/docs/{doc['id']}").json()
    blocks = D.get_blocks(got["payload"])
    types = [b["type"] for b in blocks if b["runs"] and b["runs"][0]["text"]]
    assert "number" in types


@pytest.mark.asyncio
async def test_import_empty_file_rejected(client):
    r = client.post(
        "/api/docs/import",
        files={"file": ("empty.docx", b"", _DOCX_MEDIA)},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_import_garbage_rejected(client):
    r = client.post(
        "/api/docs/import",
        files={"file": ("bad.docx", b"not a docx", _DOCX_MEDIA)},
    )
    assert r.status_code == 400
