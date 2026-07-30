"""Worker-LLM inbox-expense project suggester tests (LLM always mocked)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lazyclaw.budgets import inbox_suggest, store
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)", ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def test_valid_llm_json_maps_to_suggestion(cfg, monkeypatch):
    await store.create_project(cfg, "u1", "ClubBay")

    async def fake_chat(*a, **k):
        return {"content": '{"project_name": "ClubBay", "confidence": "high", "reason": "matches club spend"}'}
    monkeypatch.setattr(inbox_suggest, "_worker_chat", fake_chat)

    s = await inbox_suggest.suggest_expense_project(
        cfg, "u1", description="venue deposit", vendor=None, amount=50, currency="EUR",
    )
    assert s.project_name == "ClubBay"
    assert s.confidence == "high"
    assert s.source == "llm"


async def test_unknown_project_name_is_discarded(cfg, monkeypatch):
    await store.create_project(cfg, "u1", "ClubBay")

    async def fake_chat(*a, **k):
        return {"content": '{"project_name": "Invented", "confidence": "high", "reason": "?"}'}
    monkeypatch.setattr(inbox_suggest, "_worker_chat", fake_chat)

    s = await inbox_suggest.suggest_expense_project(
        cfg, "u1", description="x", vendor=None, amount=1, currency="EUR",
    )
    assert s.project_name is None
    assert s.confidence == "none"


async def test_timeout_and_garbage_never_raise(cfg, monkeypatch):
    await store.create_project(cfg, "u1", "ClubBay")

    async def slow_chat(*a, **k):
        raise asyncio.TimeoutError
    monkeypatch.setattr(inbox_suggest, "_worker_chat", slow_chat)
    s = await inbox_suggest.suggest_expense_project(
        cfg, "u1", description="x", vendor=None, amount=1, currency="EUR",
    )
    assert s.source == "none" and s.project_name is None

    async def garbage_chat(*a, **k):
        return {"content": "not json at all"}
    monkeypatch.setattr(inbox_suggest, "_worker_chat", garbage_chat)
    s = await inbox_suggest.suggest_expense_project(
        cfg, "u1", description="x", vendor=None, amount=1, currency="EUR",
    )
    assert s.source == "none"
