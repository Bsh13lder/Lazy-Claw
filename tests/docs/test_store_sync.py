"""Offline-sync primitives for the doc store (lazyclaw/docs/store.py).

Covers: soft-delete tombstone, the changes delta feed, and client-id create
idempotency. Mirrors tests/sheets/test_store_sync.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.docs import store

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


# ── Soft delete ──────────────────────────────────────────────────────────


async def test_delete_is_soft_and_sets_tombstone(cfg):
    created = await store.create_doc(cfg, "u1", "Temp")
    did = created["id"]
    assert await store.delete_doc(cfg, "u1", did) is True

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM docs WHERE id = ? AND user_id = ?", (did, "u1")
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None

    assert await store.get_doc(cfg, "u1", did) is None
    assert await store.list_docs(cfg, "u1") == []


async def test_delete_is_idempotent(cfg):
    created = await store.create_doc(cfg, "u1", "Temp")
    assert await store.delete_doc(cfg, "u1", created["id"]) is True
    assert await store.delete_doc(cfg, "u1", created["id"]) is False
    assert await store.delete_doc(cfg, "u1", "no-such-id") is False


async def test_delete_is_user_scoped(cfg):
    created = await store.create_doc(cfg, "u1", "Mine")
    assert await store.delete_doc(cfg, "u2", created["id"]) is False
    assert await store.get_doc(cfg, "u1", created["id"]) is not None


# ── Changes feed ─────────────────────────────────────────────────────────


async def test_changes_no_since_returns_all_live(cfg):
    a = await store.create_doc(cfg, "u1", "A")
    b = await store.create_doc(cfg, "u1", "B")
    res = await store.get_doc_changes(cfg, "u1", since=None)
    ids = {d["id"] for d in res["docs"]}
    assert ids == {a["id"], b["id"]}
    assert res["deleted"] == []
    assert res["now"]
    assert all("payload" not in d for d in res["docs"])


async def test_changes_includes_updated_and_tombstones_after_since(cfg):
    old = await store.create_doc(cfg, "u1", "Old")
    since = (await store.get_doc_changes(cfg, "u1", since=None))["now"]

    new = await store.create_doc(cfg, "u1", "New")
    assert await store.delete_doc(cfg, "u1", old["id"]) is True

    res = await store.get_doc_changes(cfg, "u1", since=since)
    live_ids = {d["id"] for d in res["docs"]}
    assert new["id"] in live_ids
    assert old["id"] not in live_ids
    assert old["id"] in res["deleted"]


async def test_changes_excludes_rows_older_than_since(cfg):
    await store.create_doc(cfg, "u1", "Older")
    res = await store.get_doc_changes(cfg, "u1", since="9999-01-01 00:00:00")
    assert res["docs"] == []
    assert res["deleted"] == []


async def test_changes_is_user_scoped(cfg):
    await store.create_doc(cfg, "u1", "Mine")
    res = await store.get_doc_changes(cfg, "u2", since=None)
    assert res["docs"] == []
    assert res["deleted"] == []


# ── Client-id create idempotency ─────────────────────────────────────────


async def test_create_with_client_id_uses_it(cfg):
    cid = "11111111-1111-1111-1111-111111111111"
    created = await store.create_doc(cfg, "u1", "Notes", doc_id=cid)
    assert created["id"] == cid


async def test_create_with_client_id_is_idempotent(cfg):
    cid = "22222222-2222-2222-2222-222222222222"
    first = await store.create_doc(cfg, "u1", "Notes", doc_id=cid)
    second = await store.create_doc(cfg, "u1", "Notes Again", doc_id=cid)
    assert second["id"] == first["id"]
    assert second["name"] == "Notes"
    assert len(await store.list_docs(cfg, "u1")) == 1


async def test_create_with_client_id_replay_after_delete_returns_row(cfg):
    cid = "33333333-3333-3333-3333-333333333333"
    await store.create_doc(cfg, "u1", "X", doc_id=cid)
    await store.delete_doc(cfg, "u1", cid)
    again = await store.create_doc(cfg, "u1", "X", doc_id=cid)
    assert again["id"] == cid
