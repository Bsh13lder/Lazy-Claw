"""Tests for the encrypted doc store (lazyclaw/docs/store.py).

Covers: blank create + list, snapshot round-trip, encrypted-at-rest, strict
user isolation, upsert-on-unknown-id, and delete semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.docs import snapshot as D
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


async def test_create_and_list(cfg):
    created = await store.create_doc(cfg, "u1", "Cover Letter")
    assert created["name"] == "Cover Letter"
    docs = await store.list_docs(cfg, "u1")
    assert len(docs) == 1
    assert docs[0]["id"] == created["id"]
    assert "payload" not in docs[0]  # index only


async def test_get_returns_blank_document(cfg):
    created = await store.create_doc(cfg, "u1", "Letter")
    fetched = await store.get_doc(cfg, "u1", created["id"])
    assert fetched["payload"]["body"]  # valid Univer snapshot
    assert D.get_paragraphs(fetched["payload"]) == [""]


async def test_snapshot_roundtrip(cfg):
    created = await store.create_doc(cfg, "u1", "Letter")
    did = created["id"]
    snap = (await store.get_doc(cfg, "u1", did))["payload"]
    snap = D.set_text(snap, "Dear hiring manager,\nI am writing to apply.\nRegards.")
    await store.save_doc(cfg, "u1", "Letter", snap, doc_id=did)

    out = (await store.get_doc(cfg, "u1", did))["payload"]
    assert D.get_text(out) == "Dear hiring manager,\nI am writing to apply.\nRegards."
    assert D.get_paragraphs(out) == [
        "Dear hiring manager,",
        "I am writing to apply.",
        "Regards.",
    ]


async def test_encrypted_at_rest(cfg):
    created = await store.create_doc(cfg, "u1", "Secret")
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT name, payload FROM docs WHERE id = ?", (created["id"],)
        )
        name, payload = await cur.fetchone()
    assert name == "Secret"  # plaintext for the sidebar
    assert payload.startswith("enc:"), "snapshot must be encrypted at rest"


async def test_payload_text_not_leaked_in_ciphertext(cfg):
    created = await store.create_doc(cfg, "u1", "Memo")
    snap = (await store.get_doc(cfg, "u1", created["id"]))["payload"]
    snap = D.set_text(snap, "TOP SECRET SAUCE")
    await store.save_doc(cfg, "u1", "Memo", snap, doc_id=created["id"])
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT payload FROM docs WHERE id = ?", (created["id"],)
        )
        (payload,) = await cur.fetchone()
    assert "TOP SECRET SAUCE" not in payload


async def test_user_isolation(cfg):
    created = await store.create_doc(cfg, "u1", "Mine")
    # u2 must not see or fetch u1's doc
    assert await store.list_docs(cfg, "u2") == []
    assert await store.get_doc(cfg, "u2", created["id"]) is None


async def test_save_unknown_id_creates(cfg):
    snap = D.blank_document("Imported")
    row = await store.save_doc(cfg, "u1", "Imported", snap, doc_id="given-id-123")
    assert row["id"] == "given-id-123"
    assert (await store.get_doc(cfg, "u1", "given-id-123")) is not None


async def test_delete(cfg):
    created = await store.create_doc(cfg, "u1", "Temp")
    assert await store.delete_doc(cfg, "u1", created["id"]) is True
    assert await store.get_doc(cfg, "u1", created["id"]) is None
    # deleting again / unknown id → False
    assert await store.delete_doc(cfg, "u1", created["id"]) is False


async def test_delete_is_user_scoped(cfg):
    created = await store.create_doc(cfg, "u1", "Mine")
    assert await store.delete_doc(cfg, "u2", created["id"]) is False
    assert await store.get_doc(cfg, "u1", created["id"]) is not None
