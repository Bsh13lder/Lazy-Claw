"""compress_history must reconstruct tool_calls from ENCRYPTED metadata.

Phase 2 of the 2026-06-10 audit encrypts ``agent_messages.metadata`` on
write. The compressor previously passed the raw column value straight to
``_to_llm_messages``'s ``json.loads`` — an encrypted value would silently
drop tool_calls reconstruction (try/except → debug log), breaking the
assistant(tool_calls) → tool(result) sequence OpenAI-style providers
require. These tests pin the decode in the fast path, plus legacy
plaintext compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt
from lazyclaw.crypto.key_manager import create_user_dek, get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.memory.compressor import compress_history

pytestmark = pytest.mark.asyncio

_TOOL_CALLS = [{"id": "tc1", "name": "upwork_get_messages", "arguments": {"room_id": "r1"}}]


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


def _raw_rows(key: bytes, metadata_value: str | None) -> list[tuple]:
    """Rows in the (id, role, content, tool_name, metadata) SELECT shape."""
    return [
        ("m1", "user", encrypt("check upwork", key), None, None),
        ("m2", "assistant", encrypt("", key), None, metadata_value),
        ("m3", "tool", encrypt('{"messages": []}', key), "tc1", None),
    ]


async def test_tool_calls_survive_encrypted_metadata(config):
    key = await get_user_dek(config, "u1")
    encrypted_meta = encrypt(json.dumps(_TOOL_CALLS), key)

    out = await compress_history(config, None, "u1", "s1", _raw_rows(key, encrypted_meta))

    assistant = next(m for m in out if m.role == "assistant")
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0].name == "upwork_get_messages"
    assert assistant.tool_calls[0].arguments == {"room_id": "r1"}


async def test_tool_calls_survive_legacy_plaintext_metadata(config):
    key = await get_user_dek(config, "u1")

    out = await compress_history(config, None, "u1", "s1", _raw_rows(key, json.dumps(_TOOL_CALLS)))

    assistant = next(m for m in out if m.role == "assistant")
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0].name == "upwork_get_messages"


async def test_garbage_metadata_fails_soft(config):
    key = await get_user_dek(config, "u1")

    out = await compress_history(config, None, "u1", "s1", _raw_rows(key, "enc:v1:bad:bad"))

    assistant = next(m for m in out if m.role == "assistant")
    assert assistant.tool_calls is None  # skipped, message itself intact
