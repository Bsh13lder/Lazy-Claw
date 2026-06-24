"""Offline-sync primitives for the PDF store (lazyclaw/pdf/store.py).

Covers: soft-delete tombstone, the changes delta feed (metadata only, no bytes),
and client-id create idempotency via create_pdf. Mirrors the sheets/docs sync
tests; PDF bodies are generated in-process (no binary fixtures on disk).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.pdf import store
from tests.pdf.conftest import make_text_pdf

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
    meta = await store.save_pdf(cfg, "u1", "temp.pdf", make_text_pdf())
    pid = meta["id"]
    assert await store.delete_pdf(cfg, "u1", pid) is True

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM pdf_files WHERE id = ? AND user_id = ?",
            (pid, "u1"),
        )
        row = await cur.fetchone()
    assert row is not None, "row must survive a soft delete"
    assert row[0] is not None, "deleted_at must be stamped"

    assert await store.get_pdf(cfg, "u1", pid) is None
    assert await store.list_pdfs(cfg, "u1") == []


async def test_delete_is_idempotent(cfg):
    meta = await store.save_pdf(cfg, "u1", "temp.pdf", make_text_pdf())
    assert await store.delete_pdf(cfg, "u1", meta["id"]) is True
    assert await store.delete_pdf(cfg, "u1", meta["id"]) is False
    assert await store.delete_pdf(cfg, "u1", "no-such-id") is False


async def test_delete_is_user_scoped(cfg):
    meta = await store.save_pdf(cfg, "u1", "mine.pdf", make_text_pdf())
    assert await store.delete_pdf(cfg, "u2", meta["id"]) is False
    assert await store.get_pdf(cfg, "u1", meta["id"]) is not None


# ── Changes feed ─────────────────────────────────────────────────────────


async def test_changes_no_since_returns_all_live_metadata(cfg):
    a = await store.save_pdf(cfg, "u1", "a.pdf", make_text_pdf())
    b = await store.save_pdf(cfg, "u1", "b.pdf", make_text_pdf())
    res = await store.get_pdf_changes(cfg, "u1", since=None)
    ids = {f["id"] for f in res["files"]}
    assert ids == {a["id"], b["id"]}
    assert res["deleted"] == []
    assert res["now"]
    # metadata only — never the bytes/payload blob
    for f in res["files"]:
        assert "bytes" not in f
        assert "payload" not in f
        assert "pages" in f  # metadata carries pages


async def test_changes_includes_updated_and_tombstones_after_since(cfg):
    old = await store.save_pdf(cfg, "u1", "old.pdf", make_text_pdf())
    since = (await store.get_pdf_changes(cfg, "u1", since=None))["now"]

    new = await store.save_pdf(cfg, "u1", "new.pdf", make_text_pdf())
    assert await store.delete_pdf(cfg, "u1", old["id"]) is True

    res = await store.get_pdf_changes(cfg, "u1", since=since)
    live_ids = {f["id"] for f in res["files"]}
    assert new["id"] in live_ids
    assert old["id"] not in live_ids
    assert old["id"] in res["deleted"]


async def test_changes_excludes_rows_older_than_since(cfg):
    await store.save_pdf(cfg, "u1", "older.pdf", make_text_pdf())
    res = await store.get_pdf_changes(cfg, "u1", since="9999-01-01 00:00:00")
    assert res["files"] == []
    assert res["deleted"] == []


async def test_changes_is_user_scoped(cfg):
    await store.save_pdf(cfg, "u1", "mine.pdf", make_text_pdf())
    res = await store.get_pdf_changes(cfg, "u2", since=None)
    assert res["files"] == []
    assert res["deleted"] == []


# ── Client-id create idempotency ─────────────────────────────────────────


async def test_create_with_client_id_uses_it(cfg):
    cid = "11111111-1111-1111-1111-111111111111"
    meta = await store.create_pdf(cfg, "u1", "doc.pdf", make_text_pdf(), pdf_id=cid)
    assert meta["id"] == cid


async def test_create_with_client_id_is_idempotent(cfg):
    cid = "22222222-2222-2222-2222-222222222222"
    first = await store.create_pdf(cfg, "u1", "doc.pdf", make_text_pdf(), pdf_id=cid)
    second = await store.create_pdf(
        cfg, "u1", "different.pdf", make_text_pdf(), pdf_id=cid
    )
    assert second["id"] == first["id"]
    assert second["name"] == "doc.pdf"  # original preserved
    assert len(await store.list_pdfs(cfg, "u1")) == 1


async def test_create_with_client_id_replay_after_delete_returns_row(cfg):
    cid = "33333333-3333-3333-3333-333333333333"
    await store.create_pdf(cfg, "u1", "x.pdf", make_text_pdf(), pdf_id=cid)
    await store.delete_pdf(cfg, "u1", cid)
    again = await store.create_pdf(cfg, "u1", "x.pdf", make_text_pdf(), pdf_id=cid)
    assert again["id"] == cid
