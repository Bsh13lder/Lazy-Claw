"""agent_messages.metadata encryption (2026-06-10 audit, Phase 2).

Tool-call arguments were the one plaintext leak in the encrypted message
store: ``vault_set(value="sk-...")`` landed verbatim in the ``metadata``
column while ``content`` was encrypted next to it. The codec encrypts on
write and reads tolerantly — legacy plaintext rows keep working, garbage
fails soft to ``None`` so tool_calls reconstruction is skipped without
losing the message content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt
from lazyclaw.crypto.key_manager import create_user_dek, get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.memory.metadata_codec import (
    decode_tool_metadata,
    encode_tool_metadata,
)

pytestmark = pytest.mark.asyncio

_TOOL_CALLS_JSON = json.dumps(
    [{"id": "tc1", "name": "vault_set", "arguments": {"key": "k", "value": "sk-SECRET"}}]
)


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


# ── Codec ─────────────────────────────────────────────────────────────


async def test_encode_produces_ciphertext_round_trippable(config):
    key = await get_user_dek(config, "u1")
    stored = encode_tool_metadata(_TOOL_CALLS_JSON, key)
    assert stored is not None and stored.startswith("enc:")
    assert "sk-SECRET" not in stored
    assert decrypt(stored, key) == _TOOL_CALLS_JSON


async def test_decode_round_trips_encoded_value(config):
    key = await get_user_dek(config, "u1")
    assert decode_tool_metadata(encode_tool_metadata(_TOOL_CALLS_JSON, key), key) == _TOOL_CALLS_JSON


async def test_decode_passes_legacy_plaintext_through(config):
    key = await get_user_dek(config, "u1")
    assert decode_tool_metadata(_TOOL_CALLS_JSON, key) == _TOOL_CALLS_JSON


async def test_decode_fails_soft_on_garbage_ciphertext(config):
    key = await get_user_dek(config, "u1")
    assert decode_tool_metadata("enc:v1:not-real:garbage", key) is None


async def test_codec_handles_none_and_empty(config):
    key = await get_user_dek(config, "u1")
    assert encode_tool_metadata(None, key) is None
    assert encode_tool_metadata("", key) is None
    assert decode_tool_metadata(None, key) is None
    assert decode_tool_metadata("", key) is None


# ── Backfill ──────────────────────────────────────────────────────────


async def _insert_message(config, msg_id: str, metadata: str | None) -> None:
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO agent_messages (id, user_id, chat_session_id, role, "
            "content, tool_name, metadata) VALUES (?, 'u1', 's1', 'assistant', "
            "'enc:fake', NULL, ?)",
            (msg_id, metadata),
        )
        await db.commit()


async def test_backfill_encrypts_legacy_plaintext_rows(config):
    from lazyclaw.memory.metadata_backfill import backfill_encrypt_tool_metadata

    await _insert_message(config, "m1", _TOOL_CALLS_JSON)
    await _insert_message(config, "m2", None)

    changed = await backfill_encrypt_tool_metadata(config)
    assert changed == 1

    key = await get_user_dek(config, "u1")
    async with db_session(config) as db:
        row = await (
            await db.execute("SELECT metadata FROM agent_messages WHERE id='m1'")
        ).fetchone()
    assert row[0].startswith("enc:")
    assert decrypt(row[0], key) == _TOOL_CALLS_JSON


async def test_backfill_is_idempotent(config):
    from lazyclaw.memory.metadata_backfill import backfill_encrypt_tool_metadata

    await _insert_message(config, "m1", _TOOL_CALLS_JSON)
    assert await backfill_encrypt_tool_metadata(config) == 1
    assert await backfill_encrypt_tool_metadata(config) == 0
