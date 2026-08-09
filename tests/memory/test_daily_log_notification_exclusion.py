"""Daily-log summarization skips UI-only notification chat cards."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.llm.eco_router import EcoRouter
from lazyclaw.memory.daily_log import generate_daily_summary
from lazyclaw.notifications import chat_card
from lazyclaw.runtime.session_resolver import invalidate_primary_session

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    invalidate_primary_session("u1")
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield c
    finally:
        invalidate_primary_session("u1")
        await close_pool()


async def test_generate_daily_summary_skips_notification_cards(cfg, monkeypatch):
    from datetime import date

    today = date.today().isoformat()

    # A real conversation row...
    from lazyclaw.crypto.encryption import encrypt

    key = await get_user_dek(cfg, "u1")
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO agent_messages "
            "(id, user_id, chat_session_id, role, content, tool_name, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("m1", "u1", None, "user", encrypt("plan my week", key), None, None),
        )
        await db.commit()
    # ...and a notification card written the same day.
    await chat_card.emit(cfg, "u1", {
        "id": "n-1", "kind": "task_reminder",
        "title": "SECRET-PING-MARKER", "body": "take your dose",
        "created_at": "",
    })

    captured: dict = {}

    async def fake_chat(self, messages, **kwargs):
        captured["prompt"] = messages[-1].content
        return SimpleNamespace(content="a fine day")

    monkeypatch.setattr(EcoRouter, "chat", fake_chat)
    # Keep the lazybrain mirror inert.
    monkeypatch.setattr(
        "lazyclaw.lazybrain.store.save_note",
        AsyncMock(return_value={"id": "note-1", "title": "t", "tags": []}),
    )

    summary = await generate_daily_summary(cfg, "u1", today)
    assert summary == "a fine day"
    assert "plan my week" in captured["prompt"]
    assert "SECRET-PING-MARKER" not in captured["prompt"], (
        "notification card leaked into the daily-log summarizer prompt"
    )
