"""BUG 8 — decrypt-fail rows must be flagged, not rendered as normal threads.

`decrypt_field` returns the sentinel ``"[encrypted]"`` on any decrypt
failure (wrong key / corruption). A row whose encrypted contact_handle
fails to decrypt would otherwise surface as a normal-looking thread titled
"[encrypted]" whose live read then also fails. `_row_to_dict` now sets
``decrypt_error: True`` on such rows so the client can render a distinct
"unreadable — re-sync" state instead of a broken thread.
"""

from __future__ import annotations

import pytest

from lazyclaw.comms import thread_store
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config(tmp_path):
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


async def test_healthy_thread_not_flagged(config):
    await thread_store.upsert_thread(
        config, "u1", channel="whatsapp",
        contact_handle="34611222333@s.whatsapp.net",
        contact_name="Maria", preview="hola",
    )
    threads = await thread_store.list_threads(config, "u1")
    assert len(threads) == 1
    assert threads[0]["decrypt_error"] is False


async def test_corrupt_handle_is_flagged(config):
    t = await thread_store.upsert_thread(
        config, "u1", channel="whatsapp",
        contact_handle="34611222333@s.whatsapp.net",
        contact_name="Maria", preview="hola",
    )
    # Simulate a mis-keyed / corrupted ciphertext for the handle: a value with
    # the `enc:v1:` prefix that will fail AES-GCM decryption → "[encrypted]".
    async with db_session(config) as db:
        await db.execute(
            "UPDATE channel_threads SET contact_handle=? WHERE id=?",
            ("enc:v1:AAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAA", t["id"]),
        )
        await db.commit()

    threads = await thread_store.list_threads(config, "u1")
    assert len(threads) == 1
    assert threads[0]["decrypt_error"] is True
