"""Tests for lazyclaw.comms.thread_store — encrypted CRUD + changes feed."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.comms import thread_store
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo!!")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(c, "u1", "salt-a")
    try:
        yield c
    finally:
        await close_pool()


@pytest.fixture
def user_id():
    return "u1"


async def test_upsert_creates_then_bumps(config, user_id):
    t = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34600000000",
        contact_name="Alice", preview="hello", last_seen_msg_id="m1", increment_unread=True,
    )
    assert t["unread_count"] == 1 and t["contact_name"] == "Alice"
    t2 = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34600000000",
        contact_name="Alice", preview="second", last_seen_msg_id="m2", increment_unread=True,
    )
    assert t2["id"] == t["id"] and t2["unread_count"] == 2 and t2["last_preview"] == "second"


async def test_mark_read_zeroes_unread(config, user_id):
    t = await thread_store.upsert_thread(
        config, user_id, channel="email", contact_handle="bob@x.com",
        contact_name="Bob", preview="hi", last_seen_msg_id="e1", increment_unread=True,
    )
    await thread_store.mark_thread_read(config, user_id, t["id"])
    got = await thread_store.get_thread(config, user_id, t["id"])
    assert got["unread_count"] == 0


async def test_changes_includes_deletes(config, user_id):
    t = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+1", contact_name="C",
        preview="p", last_seen_msg_id="m1", increment_unread=False,
    )
    snap = await thread_store.get_thread_changes(config, user_id, None)
    since = snap["now"]
    await thread_store.delete_thread(config, user_id, t["id"])
    delta = await thread_store.get_thread_changes(config, user_id, since)
    assert t["id"] in delta["deleted"]


async def test_list_threads_filters_by_channel(config, user_id):
    await thread_store.upsert_thread(config, user_id, channel="whatsapp", contact_handle="+1", contact_name="A", preview="p", last_seen_msg_id="m1")
    await thread_store.upsert_thread(config, user_id, channel="email", contact_handle="a@b.c", contact_name="B", preview="q", last_seen_msg_id="e1")
    wa = await thread_store.list_threads(config, user_id, channel="whatsapp")
    assert len(wa) == 1 and wa[0]["channel"] == "whatsapp"
    allt = await thread_store.list_threads(config, user_id)
    assert len(allt) == 2


async def test_none_contact_name_round_trips_as_none(config, user_id):
    """upsert with contact_name omitted must store NULL and return None, not ''."""
    t = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34700000001",
        preview="hello",
    )
    assert t["contact_name"] is None
    got = await thread_store.get_thread(config, user_id, t["id"])
    assert got["contact_name"] is None


async def test_soft_delete_then_upsert_revives(config, user_id):
    """Re-upserting a soft-deleted thread clears deleted_at and reuses the same id."""
    t = await thread_store.upsert_thread(
        config, user_id, channel="telegram", contact_handle="@carlos",
        contact_name="Carlos", preview="first",
    )
    original_id = t["id"]
    await thread_store.delete_thread(config, user_id, original_id)

    # confirm it's soft-deleted
    deleted = await thread_store.get_thread(config, user_id, original_id)
    assert deleted["deleted_at"] is not None

    # re-upsert same (channel, contact_handle)
    revived = await thread_store.upsert_thread(
        config, user_id, channel="telegram", contact_handle="@carlos",
        contact_name="Carlos", preview="revived",
    )
    assert revived["id"] == original_id
    assert revived["deleted_at"] is None
    assert revived["last_preview"] == "revived"


async def test_preserves_name_when_reupserted_with_none(config, user_id):
    """Re-upserting with contact_name=None must keep the previously stored name."""
    await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34700000002",
        contact_name="Alice", preview="first",
    )
    updated = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34700000002",
        contact_name=None, preview="newer",
    )
    assert updated["contact_name"] == "Alice"
    assert updated["last_preview"] == "newer"


async def test_cross_user_isolation(config, user_id):
    """A thread upserted for user_id must not appear for a different user_id."""
    from lazyclaw.crypto.key_manager import create_user_dek
    from lazyclaw.db.connection import db_session

    other_user = "u2"
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (other_user, "bob", "x", "salt-b"),
        )
        await db.commit()
    await create_user_dek(config, other_user, "salt-b")

    t = await thread_store.upsert_thread(
        config, user_id, channel="email", contact_handle="secret@test.com",
        contact_name="Secret", preview="private",
    )
    # other user must see no threads
    other_threads = await thread_store.list_threads(config, other_user)
    assert all(th["id"] != t["id"] for th in other_threads)
    # direct get must also return None for wrong user
    assert await thread_store.get_thread(config, other_user, t["id"]) is None


# ── Security hardening (2026-06-10 review) ─────────────────────────────────────


async def test_contact_handle_encrypted_at_rest(config, user_id):
    """contact_handle is PII (phone numbers / WhatsApp JIDs) — the raw row must
    hold an enc:v1 ciphertext plus an HMAC hash column for dedup, never the
    plaintext handle. The decrypted dict still returns the plaintext."""
    handle = "34611234567@s.whatsapp.net"
    t = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle=handle,
        contact_name="Vato", preview="hola",
    )
    assert t["contact_handle"] == handle  # API surface unchanged
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT contact_handle, contact_handle_hash FROM channel_threads WHERE id=?",
            (t["id"],),
        )
        row = await cur.fetchone()
    assert row["contact_handle"].startswith("enc:v1:")
    assert handle not in row["contact_handle"]
    assert row["contact_handle_hash"] is not None
    assert handle not in row["contact_handle_hash"]


async def test_encrypted_handle_dedup_still_works(config, user_id):
    """Dedup by (user, channel, handle) must survive encryption — the HMAC hash
    lookup must map a second upsert onto the same row."""
    t1 = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34699999999",
        preview="first", increment_unread=True,
    )
    t2 = await thread_store.upsert_thread(
        config, user_id, channel="whatsapp", contact_handle="+34699999999",
        preview="second", increment_unread=True,
    )
    assert t2["id"] == t1["id"]
    assert t2["unread_count"] == 2


async def test_legacy_plaintext_row_upgraded_on_upsert(config, user_id):
    """Rows written before handle encryption (plaintext handle, NULL hash) must
    be claimed and upgraded in place on the next upsert — no duplicate thread."""
    handle = "legacy@old.com"
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO channel_threads "
            "(id,user_id,channel,contact_handle,unread_count,last_activity,"
            "created_at,updated_at,deleted_at) "
            "VALUES ('legacy-1',?,'email',?,1,'2026-01-01T00:00:00+00:00',"
            "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',NULL)",
            (user_id, handle),
        )
        await db.commit()

    t = await thread_store.upsert_thread(
        config, user_id, channel="email", contact_handle=handle,
        preview="new mail", increment_unread=True,
    )
    assert t["id"] == "legacy-1"          # same row, not a duplicate
    assert t["unread_count"] == 2
    assert t["contact_handle"] == handle  # still decodes to the plaintext
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT contact_handle, contact_handle_hash FROM channel_threads "
            "WHERE id='legacy-1'",
        )
        row = await cur.fetchone()
    assert row["contact_handle"].startswith("enc:v1:")  # upgraded in place
    assert row["contact_handle_hash"] is not None


async def test_upsert_rejects_invalid_channel(config, user_id):
    """Channel names must be sane tokens — path traversal & junk rejected."""
    for bad in ("../../etc", "What Sapp", "", "A" * 64):
        with pytest.raises(ValueError):
            await thread_store.upsert_thread(
                config, user_id, channel=bad, contact_handle="+34600000001",
            )


async def test_upsert_readback_failure_raises(config, user_id, monkeypatch):
    """The post-upsert readback guard must raise (not silently return None)
    even under `python -O` — i.e. it must NOT be a bare assert."""
    async def _none(*a, **kw):
        return None
    monkeypatch.setattr(thread_store, "get_thread", _none)
    with pytest.raises(RuntimeError):
        await thread_store.upsert_thread(
            config, user_id, channel="whatsapp", contact_handle="+34600000002",
        )
