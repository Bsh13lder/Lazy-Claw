"""Tests for the encrypted sheet store (lazyclaw/sheets/store.py).

Covers: blank create + list, snapshot round-trip, encrypted-at-rest, strict
user isolation, upsert-on-unknown-id, and delete semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.sheets import store
from lazyclaw.sheets import snapshot as S

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        for uid, salt in (("u1", "salt-a"), ("u2", "salt-b")):
            await db.execute(
                "INSERT INTO users (id, username, password_hash, encryption_salt) "
                "VALUES (?, ?, ?, ?)",
                (uid, uid, "x", salt),
            )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def test_create_and_list(cfg):
    created = await store.create_sheet(cfg, "u1", "Budget")
    assert created["name"] == "Budget"
    sheets = await store.list_sheets(cfg, "u1")
    assert len(sheets) == 1
    assert sheets[0]["id"] == created["id"]
    assert "payload" not in sheets[0]  # index only


async def test_get_returns_blank_workbook(cfg):
    created = await store.create_sheet(cfg, "u1", "Budget")
    fetched = await store.get_sheet(cfg, "u1", created["id"])
    assert fetched["payload"]["sheets"]  # valid Univer snapshot
    assert S.sheet_names(fetched["payload"]) == ["Sheet1"]


async def test_snapshot_roundtrip(cfg):
    created = await store.create_sheet(cfg, "u1", "Budget")
    sid = created["id"]
    snap = (await store.get_sheet(cfg, "u1", sid))["payload"]
    snap = S.set_cells(snap, [
        {"row": 0, "col": 0, "value": 10},
        {"row": 1, "col": 0, "value": 20},
        {"row": 2, "col": 0, "formula": "=SUM(A1:A2)"},
    ])
    await store.save_sheet(cfg, "u1", "Budget", snap, sheet_id=sid)

    out = (await store.get_sheet(cfg, "u1", sid))["payload"]
    assert S.cell_display(S.get_cell(out, 0, 0)) == 10
    assert S.get_cell(out, 2, 0)["f"] == "=SUM(A1:A2)"


async def test_encrypted_at_rest(cfg):
    created = await store.create_sheet(cfg, "u1", "Secret")
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT name, payload FROM sheets WHERE id = ?", (created["id"],)
        )
        name, payload = await cur.fetchone()
    assert name == "Secret"  # plaintext for the sidebar
    assert payload.startswith("enc:"), "snapshot must be encrypted at rest"


async def test_user_isolation(cfg):
    created = await store.create_sheet(cfg, "u1", "Mine")
    # u2 must not see or fetch u1's sheet
    assert await store.list_sheets(cfg, "u2") == []
    assert await store.get_sheet(cfg, "u2", created["id"]) is None


async def test_save_unknown_id_creates(cfg):
    snap = S.blank_workbook("Imported")
    row = await store.save_sheet(cfg, "u1", "Imported", snap, sheet_id="given-id-123")
    assert row["id"] == "given-id-123"
    assert (await store.get_sheet(cfg, "u1", "given-id-123")) is not None


async def test_delete(cfg):
    created = await store.create_sheet(cfg, "u1", "Temp")
    assert await store.delete_sheet(cfg, "u1", created["id"]) is True
    assert await store.get_sheet(cfg, "u1", created["id"]) is None
    # deleting again / unknown id → False
    assert await store.delete_sheet(cfg, "u1", created["id"]) is False


async def test_delete_is_user_scoped(cfg):
    created = await store.create_sheet(cfg, "u1", "Mine")
    assert await store.delete_sheet(cfg, "u2", created["id"]) is False
    assert await store.get_sheet(cfg, "u1", created["id"]) is not None
