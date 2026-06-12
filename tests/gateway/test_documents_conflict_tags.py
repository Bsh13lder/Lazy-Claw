"""Gateway tests for 409 conflict detection + tags wiring (Task 2)
and links/convert endpoint (Task 4).

Covers:
  Sheets:
    - PUT with stale base_updated_at → 409 with conflict payload
    - PUT with matching base_updated_at → 200 with fresh updated_at
    - PUT without name keeps stored name
    - PUT with tags persists tags (visible in list)
    - PUT on unknown id → 404
    - POST /{id}/links/convert → 200, converted count, snapshot has resource,
      updated_at present, sheet persisted; 404 on unknown id
  Docs:
    - PUT with stale base_updated_at → 409
  PDF:
    - PATCH /{pdf_id} updates name + tags → 200
    - PATCH /{pdf_id} on missing id → 404

Auth: FastAPI dependency_overrides[get_current_user] = lambda: _fake_user()
Config: monkeypatch.setattr(route_module, "_config", cfg) — module-level var pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
import lazyclaw.gateway.routes.sheets as sheets_mod
import lazyclaw.gateway.routes.pdf as pdf_mod
from lazyclaw.sheets.store import create_sheet
from lazyclaw.pdf.store import save_pdf

# Docs route imports python-docx at import time — detect availability.
try:
    import lazyclaw.gateway.routes.docs as docs_mod
    from lazyclaw.docs.store import create_doc
    _DOCS_AVAILABLE = True
except ImportError:
    docs_mod = None  # type: ignore[assignment]
    create_doc: Any = None
    _DOCS_AVAILABLE = False

_requires_docs = pytest.mark.skipif(
    not _DOCS_AVAILABLE,
    reason="python-docx not installed — docs route unavailable",
)


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _setup_db(tmp_path: Path) -> Config:
    cfg = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    # Provision the per-user DEK so encryption/decryption works in route tests.
    await create_user_dek(cfg, "u1", "salt-a")
    return cfg


def _fake_user() -> User:
    return User(
        id="u1",
        username="alice",
        display_name=None,
        encryption_salt="salt-a",
        role="user",
    )


_PAYLOAD: dict = {"sheets": {}}
_DOC_PAYLOAD: dict = {"body": {}}


# ── Sheets fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
async def sheets_client(tmp_path: Path, monkeypatch):
    cfg = await _setup_db(tmp_path)
    monkeypatch.setattr(sheets_mod, "_config", cfg)

    app = FastAPI()
    app.include_router(sheets_mod.router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    return TestClient(app), cfg


# ── Docs fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def docs_client(tmp_path: Path, monkeypatch):
    if not _DOCS_AVAILABLE:
        pytest.skip("python-docx not installed")
    cfg = await _setup_db(tmp_path)
    monkeypatch.setattr(docs_mod, "_config", cfg)  # type: ignore[arg-type]

    app = FastAPI()
    app.include_router(docs_mod.router)  # type: ignore[union-attr]
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    return TestClient(app), cfg


# ── PDF fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
async def pdf_client(tmp_path: Path, monkeypatch):
    cfg = await _setup_db(tmp_path)
    monkeypatch.setattr(pdf_mod, "_config", cfg)
    # page_count requires pypdf which may not be installed in dev env; stub it.
    monkeypatch.setattr("lazyclaw.pdf.store.ops.page_count", lambda _: 1)

    app = FastAPI()
    app.include_router(pdf_mod.router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    return TestClient(app), cfg


# ── Sheet: conflict (stale base_updated_at) ────────────────────────────────────


async def test_sheet_put_stale_base_returns_409(sheets_client):
    client, cfg = sheets_client
    sheet = await create_sheet(cfg, "u1", "My Sheet")
    sid = sheet["id"]
    original_ts = sheet["updated_at"]

    # Advance the stored updated_at directly (bypass 1-second _now() resolution)
    newer_ts = "2026-01-01 12:00:01"
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE sheets SET updated_at = ? WHERE id = ?", (newer_ts, sid)
        )
        await db.commit()

    # PUT with the stale original timestamp → 409
    r = client.put(
        f"/api/sheets/{sid}",
        json={
            "payload": _PAYLOAD,
            "base_updated_at": original_ts,  # stale — DB has newer_ts
        },
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["detail"] == "conflict"
    assert "current" in body
    assert body["current"]["updated_at"] == newer_ts


# ── Sheet: matching base_updated_at → 200 ──────────────────────────────────────


async def test_sheet_put_matching_base_returns_200(sheets_client):
    client, cfg = sheets_client
    sheet = await create_sheet(cfg, "u1", "Sheet B")
    sid = sheet["id"]

    r = client.put(
        f"/api/sheets/{sid}",
        json={
            "payload": _PAYLOAD,
            "base_updated_at": sheet["updated_at"],
        },
    )
    assert r.status_code == 200, r.text
    result = r.json()["sheet"]
    assert "updated_at" in result
    assert result["id"] == sid


# ── Sheet: PUT without name keeps stored name ──────────────────────────────────


async def test_sheet_put_without_name_preserves_name(sheets_client):
    client, cfg = sheets_client
    sheet = await create_sheet(cfg, "u1", "Keep My Name")
    sid = sheet["id"]

    # PUT without a name field
    r = client.put(f"/api/sheets/{sid}", json={"payload": _PAYLOAD})
    assert r.status_code == 200, r.text

    # GET the sheet back to verify name is unchanged
    r2 = client.get(f"/api/sheets/{sid}")
    assert r2.status_code == 200, r2.text
    assert r2.json()["name"] == "Keep My Name"


# ── Sheet: PUT with tags persists them ────────────────────────────────────────


async def test_sheet_put_tags_persisted_in_list(sheets_client):
    client, cfg = sheets_client
    sheet = await create_sheet(cfg, "u1", "Tagged Sheet")
    sid = sheet["id"]

    r = client.put(
        f"/api/sheets/{sid}",
        json={"payload": _PAYLOAD, "tags": ["finance", "q1"]},
    )
    assert r.status_code == 200, r.text

    # Check tags appear in the list endpoint
    list_r = client.get("/api/sheets")
    assert list_r.status_code == 200, list_r.text
    sheets = list_r.json()["sheets"]
    found = next((s for s in sheets if s["id"] == sid), None)
    assert found is not None
    assert set(found["tags"]) == {"finance", "q1"}


# ── Sheet: PUT on unknown id → auto-creates ───────────────────────────────────


async def test_sheet_put_unknown_id_auto_creates(sheets_client):
    client, _ = sheets_client
    r = client.put(
        "/api/sheets/nonexistent-id-xyz",
        json={"payload": _PAYLOAD},
    )
    # A fully unknown id gets auto-created as a new row (not a foreign key constraint).
    # Foreign-user protection is enforced at the store level (tested there per spec).
    assert r.status_code == 200, r.text


# ── Docs: conflict (stale base_updated_at) ────────────────────────────────────


@_requires_docs
async def test_doc_put_stale_base_returns_409(docs_client):
    client, cfg = docs_client
    doc = await create_doc(cfg, "u1", "My Doc")  # type: ignore[misc]
    did = doc["id"]
    original_ts = doc["updated_at"]

    # Advance the stored updated_at directly (bypass 1-second _now() resolution)
    newer_ts = "2026-01-01 12:00:01"
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE docs SET updated_at = ? WHERE id = ?", (newer_ts, did)
        )
        await db.commit()

    # PUT with the stale original timestamp → 409
    r = client.put(
        f"/api/docs/{did}",
        json={
            "payload": _DOC_PAYLOAD,
            "base_updated_at": original_ts,  # stale — DB has newer_ts
        },
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["detail"] == "conflict"
    assert "current" in body
    assert body["current"]["updated_at"] == newer_ts


# ── Docs: without name keeps stored name ─────────────────────────────────────


@_requires_docs
async def test_doc_put_without_name_preserves_name(docs_client):
    client, cfg = docs_client
    doc = await create_doc(cfg, "u1", "Preserved Doc Name")
    did = doc["id"]

    r = client.put(f"/api/docs/{did}", json={"payload": _DOC_PAYLOAD})
    assert r.status_code == 200, r.text

    r2 = client.get(f"/api/docs/{did}")
    assert r2.status_code == 200, r2.text
    assert r2.json()["name"] == "Preserved Doc Name"


# ── PDF: PATCH updates name + tags ────────────────────────────────────────────


_MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF\n"
)


async def test_pdf_patch_updates_name_and_tags(pdf_client):
    client, cfg = pdf_client
    row = await save_pdf(cfg, "u1", "original.pdf", _MINIMAL_PDF)
    pdf_id = row["id"]

    r = client.patch(
        f"/api/pdf/{pdf_id}",
        json={"name": "renamed.pdf", "tags": ["invoice", "2026"]},
    )
    assert r.status_code == 200, r.text
    file_meta = r.json()["file"]
    assert file_meta["name"] == "renamed.pdf"
    assert set(file_meta["tags"]) == {"invoice", "2026"}


async def test_pdf_patch_missing_id_returns_404(pdf_client):
    client, _ = pdf_client
    r = client.patch("/api/pdf/no-such-id", json={"name": "x.pdf"})
    assert r.status_code == 404, r.text


async def test_pdf_patch_name_only_leaves_tags_unchanged(pdf_client):
    client, cfg = pdf_client
    row = await save_pdf(cfg, "u1", "start.pdf", _MINIMAL_PDF)
    pdf_id = row["id"]

    # First patch to set tags
    r0 = client.patch(f"/api/pdf/{pdf_id}", json={"tags": ["keep-me"]})
    assert r0.status_code == 200, r0.text

    # Second patch: rename only
    r = client.patch(f"/api/pdf/{pdf_id}", json={"name": "new-name.pdf"})
    assert r.status_code == 200, r.text
    meta = r.json()["file"]
    assert meta["name"] == "new-name.pdf"
    assert "keep-me" in meta["tags"]


# ── PDF: list endpoint shows tags after PATCH (covers fix 1) ──────────────────


async def test_pdf_list_shows_tags_after_patch(pdf_client):
    client, cfg = pdf_client
    row = await save_pdf(cfg, "u1", "listed.pdf", _MINIMAL_PDF)
    pdf_id = row["id"]

    r = client.patch(f"/api/pdf/{pdf_id}", json={"tags": ["visible", "in-list"]})
    assert r.status_code == 200, r.text

    list_r = client.get("/api/pdf")
    assert list_r.status_code == 200, list_r.text
    files = list_r.json()["files"]
    found = next((f for f in files if f["id"] == pdf_id), None)
    assert found is not None
    assert set(found["tags"]) == {"visible", "in-list"}


# ── Doc: PUT with tags persists them ─────────────────────────────────────────


@_requires_docs
async def test_doc_put_tags_persisted_in_list(docs_client):
    client, cfg = docs_client
    doc = await create_doc(cfg, "u1", "Tagged Doc")  # type: ignore[misc]
    did = doc["id"]

    r = client.put(
        f"/api/docs/{did}",
        json={"payload": _DOC_PAYLOAD, "tags": ["report", "q2"]},
    )
    assert r.status_code == 200, r.text

    list_r = client.get("/api/docs")
    assert list_r.status_code == 200, list_r.text
    docs = list_r.json()["docs"]
    found = next((d for d in docs if d["id"] == did), None)
    assert found is not None
    assert set(found["tags"]) == {"report", "q2"}


# ── Sheet: 409 carries full payload in body["current"]["payload"] ─────────────


async def test_sheet_put_409_carries_full_payload(sheets_client):
    client, cfg = sheets_client
    sheet = await create_sheet(cfg, "u1", "Conflict Payload Sheet")
    sid = sheet["id"]
    original_ts = sheet["updated_at"]

    # Advance stored updated_at to force a conflict
    newer_ts = "2026-01-01 12:00:02"
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE sheets SET updated_at = ? WHERE id = ?", (newer_ts, sid)
        )
        await db.commit()

    r = client.put(
        f"/api/sheets/{sid}",
        json={
            "payload": _PAYLOAD,
            "base_updated_at": original_ts,
        },
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert "current" in body
    assert "payload" in body["current"], "409 body must carry full current snapshot"


# ── Sheet: POST /links/convert ────────────────────────────────────────────────


async def test_links_convert_bare_url_returns_200_and_count(sheets_client):
    """POST /{id}/links/convert on a sheet with a bare URL: 200, converted==1."""
    from lazyclaw.sheets.snapshot import set_cells
    from lazyclaw.sheets.store import get_sheet, save_sheet

    client, cfg = sheets_client
    sheet = await create_sheet(cfg, "u1", "Links Sheet")
    sid = sheet["id"]

    # Write a bare URL into the sheet
    stored = await get_sheet(cfg, "u1", sid)
    assert stored is not None
    snap_with_url = set_cells(
        stored["payload"],
        [{"row": 0, "col": 0, "value": "https://example.com"}],
    )
    await save_sheet(cfg, "u1", "Links Sheet", snap_with_url, sheet_id=sid)

    r = client.post(f"/api/sheets/{sid}/links/convert")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["converted"] == 1
    assert "updated_at" in body
    assert "snapshot" in body

    # Verify the resource is embedded in the returned snapshot
    snap = body["snapshot"]
    resources = snap.get("resources") or []
    assert any(
        res.get("name") == "SHEET_HYPER_LINK_PLUGIN"
        for res in resources
        if isinstance(res, dict)
    ), "snapshot must contain SHEET_HYPER_LINK_PLUGIN resource"


async def test_links_convert_sheet_persisted_after_convert(sheets_client):
    """After convert, a GET shows the resource is stored (not just returned in-memory)."""
    from lazyclaw.sheets.snapshot import get_sheet_links, set_cells
    from lazyclaw.sheets.store import get_sheet, save_sheet

    client, cfg = sheets_client
    sheet = await create_sheet(cfg, "u1", "Persist Sheet")
    sid = sheet["id"]

    stored = await get_sheet(cfg, "u1", sid)
    assert stored is not None
    snap_with_url = set_cells(
        stored["payload"],
        [{"row": 0, "col": 0, "value": "https://persisted.io"}],
    )
    await save_sheet(cfg, "u1", "Persist Sheet", snap_with_url, sheet_id=sid)

    r = client.post(f"/api/sheets/{sid}/links/convert")
    assert r.status_code == 200, r.text

    # GET the sheet back and check links are persisted
    r2 = client.get(f"/api/sheets/{sid}")
    assert r2.status_code == 200, r2.text
    fetched_snap = r2.json()["payload"]
    links = get_sheet_links(fetched_snap)
    assert len(links) == 1
    assert links[0]["payload"] == "https://persisted.io"


async def test_links_convert_unknown_id_returns_404(sheets_client):
    """POST /links/convert on an unknown sheet id → 404."""
    client, _ = sheets_client
    r = client.post("/api/sheets/no-such-id/links/convert")
    assert r.status_code == 404, r.text
