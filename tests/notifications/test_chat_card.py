"""Tests for the chat-card leg (lazyclaw/notifications/chat_card.py).

Contract:
  - emits ONE assistant-role row into the user's PRIMARY chat session;
  - content is the clean ``{title}\\n{body}`` text, AES-encrypted at rest
    with the user DEK (same envelope as the agent's turn writer);
  - metadata carries the ``notification_card`` marker + kind, encrypted via
    the metadata codec;
  - never raises into the ping path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.memory.chat_message_store import (
    NOTIFICATION_CARD_MARKER,
    is_notification_card_metadata,
)
from lazyclaw.memory.metadata_codec import decode_tool_metadata
from lazyclaw.notifications import chat_card
from lazyclaw.runtime.session_resolver import (
    get_primary_session_id,
    invalidate_primary_session,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    invalidate_primary_session("u1")
    try:
        yield c
    finally:
        invalidate_primary_session("u1")
        await close_pool()


def _notif(**over) -> dict:
    base = {
        "id": "n-1",
        "kind": "task_reminder",
        "title": "Medicine",
        "body": "Take your dose now",
        "created_at": "2026-08-09T10:00:00+00:00",
    }
    base.update(over)
    return base


async def _rows(cfg):
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT id, user_id, chat_session_id, role, content, tool_name, "
            "metadata FROM agent_messages WHERE user_id = 'u1'",
        )
        return await cur.fetchall()


async def test_emit_writes_encrypted_assistant_row_in_primary_session(cfg):
    msg_id = await chat_card.emit(cfg, "u1", _notif())
    assert msg_id

    rows = await _rows(cfg)
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == msg_id
    assert row[3] == "assistant"
    assert row[2] == await get_primary_session_id(cfg, "u1")
    assert row[5] is None  # tool_name unused for cards

    # Content encrypted at rest, decrypts to {title}\n{body}.
    assert row[4].startswith("enc:"), "content must be encrypted at rest"
    key = await get_user_dek(cfg, "u1")
    assert decrypt_field(row[4], key) == "Medicine\nTake your dose now"

    # Metadata encrypted at rest, decodes to the marker dict.
    assert row[6] is not None and row[6].startswith("enc:")
    meta = json.loads(decode_tool_metadata(row[6], key))
    assert meta[NOTIFICATION_CARD_MARKER] is True
    assert meta["kind"] == "task_reminder"
    assert is_notification_card_metadata(decode_tool_metadata(row[6], key))


async def test_emit_bumps_session_message_count(cfg):
    await chat_card.emit(cfg, "u1", _notif())
    session_id = await get_primary_session_id(cfg, "u1")
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT message_count FROM agent_chat_sessions WHERE id = ?",
            (session_id,),
        )
        row = await cur.fetchone()
    assert row[0] == 1


async def test_emit_drops_title_the_body_already_starts_with(cfg):
    await chat_card.emit(cfg, "u1", _notif(
        title="Task complete", body="Task complete\nAll six cities scraped",
    ))
    key = await get_user_dek(cfg, "u1")
    rows = await _rows(cfg)
    assert decrypt_field(rows[0][4], key) == (
        "Task complete\nAll six cities scraped"
    )


async def test_emit_empty_text_writes_nothing(cfg):
    assert await chat_card.emit(cfg, "u1", _notif(title="", body="")) is None
    assert await _rows(cfg) == []


async def test_emit_never_raises_on_failure(cfg, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "lazyclaw.notifications.chat_card.append_assistant_message", _boom,
    )
    # Must swallow — the ping path can never crash on the chat leg.
    assert await chat_card.emit(cfg, "u1", _notif()) is None


async def test_compose_card_text_shapes():
    assert chat_card.compose_card_text("T", "B") == "T\nB"
    assert chat_card.compose_card_text("", "B") == "B"
    assert chat_card.compose_card_text("T", "") == "T"
    assert chat_card.compose_card_text("T", "T and more") == "T and more"
    assert chat_card.compose_card_text(None, None) == ""
