"""Tests for the encrypted PDF store (lazyclaw/pdf/store.py).

Covers save + list, base64 round-trip integrity, encrypted-at-rest, page-count
computation, strict user isolation, upsert-on-unknown-id, and delete.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.pdf import ops, store
from tests.pdf.conftest import make_multipage_pdf, make_text_pdf

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


async def test_save_and_list(cfg):
    data = make_text_pdf()
    meta = await store.save_pdf(cfg, "u1", "doc.pdf", data)
    assert meta["name"] == "doc.pdf"
    assert meta["pages"] == 1
    rows = await store.list_pdfs(cfg, "u1")
    assert len(rows) == 1
    assert rows[0]["id"] == meta["id"]
    assert rows[0]["pages"] == 1
    assert "bytes" not in rows[0]  # index only


async def test_pages_computed_for_multipage(cfg):
    data = make_multipage_pdf(3)
    meta = await store.save_pdf(cfg, "u1", "big.pdf", data)
    assert meta["pages"] >= 3


async def test_base64_roundtrip_integrity(cfg):
    data = make_text_pdf("Round trip integrity sentinel.")
    meta = await store.save_pdf(cfg, "u1", "rt.pdf", data)
    fetched = await store.get_pdf(cfg, "u1", meta["id"])
    assert fetched is not None
    assert fetched["bytes"] == data  # byte-for-byte identical
    assert "Round trip integrity sentinel" in ops.extract_text(fetched["bytes"])


async def test_encrypted_at_rest(cfg):
    data = make_text_pdf()
    meta = await store.save_pdf(cfg, "u1", "secret.pdf", data)
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT name, payload FROM pdf_files WHERE id = ?", (meta["id"],)
        )
        name, payload = await cur.fetchone()
    assert name == "secret.pdf"  # plaintext for the sidebar
    assert payload.startswith("enc:"), "payload must be encrypted at rest"
    # the raw PDF magic must not appear in the stored ciphertext
    assert "%PDF" not in payload


async def test_user_isolation(cfg):
    data = make_text_pdf()
    meta = await store.save_pdf(cfg, "u1", "mine.pdf", data)
    assert await store.list_pdfs(cfg, "u2") == []
    assert await store.get_pdf(cfg, "u2", meta["id"]) is None


async def test_save_unknown_id_creates(cfg):
    data = make_text_pdf()
    meta = await store.save_pdf(cfg, "u1", "given.pdf", data, pdf_id="given-id-123")
    assert meta["id"] == "given-id-123"
    assert (await store.get_pdf(cfg, "u1", "given-id-123")) is not None


async def test_save_update_existing(cfg):
    data1 = make_text_pdf("version one")
    meta = await store.save_pdf(cfg, "u1", "v.pdf", data1)
    data2 = make_multipage_pdf(2)
    await store.save_pdf(cfg, "u1", "v.pdf", data2, pdf_id=meta["id"])
    fetched = await store.get_pdf(cfg, "u1", meta["id"])
    assert fetched["bytes"] == data2
    assert fetched["pages"] >= 2
    # still only one row
    assert len(await store.list_pdfs(cfg, "u1")) == 1


async def test_save_empty_raises(cfg):
    with pytest.raises(ValueError):
        await store.save_pdf(cfg, "u1", "x.pdf", b"")


async def test_delete(cfg):
    data = make_text_pdf()
    meta = await store.save_pdf(cfg, "u1", "temp.pdf", data)
    assert await store.delete_pdf(cfg, "u1", meta["id"]) is True
    assert await store.get_pdf(cfg, "u1", meta["id"]) is None
    assert await store.delete_pdf(cfg, "u1", meta["id"]) is False


async def test_delete_is_user_scoped(cfg):
    data = make_text_pdf()
    meta = await store.save_pdf(cfg, "u1", "mine.pdf", data)
    assert await store.delete_pdf(cfg, "u2", meta["id"]) is False
    assert await store.get_pdf(cfg, "u1", meta["id"]) is not None
