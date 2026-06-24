"""Offline-sync primitives for the sheet store (lazyclaw/sheets/store.py).

Covers: soft-delete tombstone (row preserved, list/get hide it), the changes
delta feed (live + tombstoned after `since`, older excluded, no-since full
sync), and client-id create idempotency for outbox replay safety.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.sheets import store

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
    created = await store.create_sheet(cfg, "u1", "Temp")
    sid = created["id"]
    assert await store.delete_sheet(cfg, "u1", sid) is True

    # The row is preserved (tombstoned), not removed.
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM sheets WHERE id = ? AND user_id = ?", (sid, "u1")
        )
        row = await cur.fetchone()
    assert row is not None, "row must survive a soft delete"
    assert row[0] is not None, "deleted_at must be stamped"

    # Normal queries treat it as gone.
    assert await store.get_sheet(cfg, "u1", sid) is None
    assert await store.list_sheets(cfg, "u1") == []


async def test_delete_is_idempotent(cfg):
    created = await store.create_sheet(cfg, "u1", "Temp")
    assert await store.delete_sheet(cfg, "u1", created["id"]) is True
    # second delete / unknown id → False (already tombstoned)
    assert await store.delete_sheet(cfg, "u1", created["id"]) is False
    assert await store.delete_sheet(cfg, "u1", "no-such-id") is False


async def test_delete_is_user_scoped(cfg):
    created = await store.create_sheet(cfg, "u1", "Mine")
    assert await store.delete_sheet(cfg, "u2", created["id"]) is False
    assert await store.get_sheet(cfg, "u1", created["id"]) is not None


# ── Changes feed ─────────────────────────────────────────────────────────


async def test_changes_no_since_returns_all_live(cfg):
    a = await store.create_sheet(cfg, "u1", "A")
    b = await store.create_sheet(cfg, "u1", "B")
    res = await store.get_sheet_changes(cfg, "u1", since=None)
    ids = {s["id"] for s in res["sheets"]}
    assert ids == {a["id"], b["id"]}
    assert res["deleted"] == []
    assert res["now"]
    # no payload in changes (index only)
    assert all("payload" not in s for s in res["sheets"])


async def test_changes_includes_updated_and_tombstones_after_since(cfg):
    old = await store.create_sheet(cfg, "u1", "Old")
    # Snapshot the cursor AFTER the old sheet exists.
    since = (await store.get_sheet_changes(cfg, "u1", since=None))["now"]

    # New sheet + a delete of the old one, both strictly after `since`.
    new = await store.create_sheet(cfg, "u1", "New")
    assert await store.delete_sheet(cfg, "u1", old["id"]) is True

    res = await store.get_sheet_changes(cfg, "u1", since=since)
    live_ids = {s["id"] for s in res["sheets"]}
    assert new["id"] in live_ids
    assert old["id"] not in live_ids       # old is now a tombstone, not live
    assert old["id"] in res["deleted"]     # surfaced as deleted


async def test_changes_excludes_rows_older_than_since(cfg):
    await store.create_sheet(cfg, "u1", "Older")
    # `since` in the far future → nothing changed after it.
    res = await store.get_sheet_changes(cfg, "u1", since="9999-01-01 00:00:00")
    assert res["sheets"] == []
    assert res["deleted"] == []


async def test_changes_is_user_scoped(cfg):
    await store.create_sheet(cfg, "u1", "Mine")
    res = await store.get_sheet_changes(cfg, "u2", since=None)
    assert res["sheets"] == []
    assert res["deleted"] == []


# ── Client-id create idempotency ─────────────────────────────────────────


async def test_create_with_client_id_uses_it(cfg):
    cid = "11111111-1111-1111-1111-111111111111"
    created = await store.create_sheet(cfg, "u1", "Budget", sheet_id=cid)
    assert created["id"] == cid


async def test_create_with_client_id_is_idempotent(cfg):
    cid = "22222222-2222-2222-2222-222222222222"
    first = await store.create_sheet(cfg, "u1", "Budget", sheet_id=cid)
    second = await store.create_sheet(cfg, "u1", "Budget Again", sheet_id=cid)
    # Same row returned, no duplicate, original name preserved.
    assert second["id"] == first["id"]
    assert second["name"] == "Budget"
    assert len(await store.list_sheets(cfg, "u1")) == 1


async def test_create_with_client_id_replay_after_delete_returns_row(cfg):
    cid = "33333333-3333-3333-3333-333333333333"
    await store.create_sheet(cfg, "u1", "X", sheet_id=cid)
    await store.delete_sheet(cfg, "u1", cid)
    # Outbox replay of the same create must not raise / duplicate.
    again = await store.create_sheet(cfg, "u1", "X", sheet_id=cid)
    assert again["id"] == cid
