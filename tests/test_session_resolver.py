"""Primary session resolver — shared history bucket across channels.

Guarantees:
  1. First call creates (or promotes) exactly one primary row per user.
  2. Second call returns the same id (idempotent + cached).
  3. invalidate_primary_session() forgets the cache without touching the DB.
  4. Concurrent callers converge on the same primary id.
  5. A pre-existing non-primary session gets promoted instead of creating a
     fresh "Main" row — preserves the user's first conversation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.runtime import session_resolver


@pytest.fixture
async def tmp_config(tmp_path: Path):
    """Spin up a fresh SQLite DB in a temp dir for each test."""
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    # Ensure a user row exists — chat_sessions has a FK to users.
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-1", "alice", "x", "salt"),
        )
        await db.commit()
    session_resolver.clear_cache()
    try:
        yield cfg
    finally:
        session_resolver.clear_cache()
        await close_pool()


@pytest.mark.asyncio
async def test_first_call_creates_primary(tmp_config: Config) -> None:
    sid = await session_resolver.get_primary_session_id(tmp_config, "user-1")
    assert sid

    async with db_session(tmp_config) as db:
        row = await db.execute(
            "SELECT id, title, is_primary FROM agent_chat_sessions "
            "WHERE user_id = ?",
            ("user-1",),
        )
        rows = await row.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == sid
    assert rows[0][1] == "Main"
    assert rows[0][2] == 1


@pytest.mark.asyncio
async def test_second_call_is_idempotent(tmp_config: Config) -> None:
    first = await session_resolver.get_primary_session_id(tmp_config, "user-1")
    second = await session_resolver.get_primary_session_id(tmp_config, "user-1")
    assert first == second

    async with db_session(tmp_config) as db:
        row = await db.execute(
            "SELECT COUNT(*) FROM agent_chat_sessions WHERE user_id = ?",
            ("user-1",),
        )
        count = (await row.fetchone())[0]
    assert count == 1


@pytest.mark.asyncio
async def test_invalidate_forces_reread(tmp_config: Config) -> None:
    first = await session_resolver.get_primary_session_id(tmp_config, "user-1")
    session_resolver.invalidate_primary_session("user-1")

    # Cache is clear — but the DB still has the row, so the next call must
    # return the same id (not create a second primary).
    second = await session_resolver.get_primary_session_id(tmp_config, "user-1")
    assert first == second


@pytest.mark.asyncio
async def test_concurrent_calls_converge(tmp_config: Config) -> None:
    session_resolver.clear_cache()
    ids = await asyncio.gather(*(
        session_resolver.get_primary_session_id(tmp_config, "user-1")
        for _ in range(5)
    ))
    assert len(set(ids)) == 1

    async with db_session(tmp_config) as db:
        row = await db.execute(
            "SELECT COUNT(*) FROM agent_chat_sessions "
            "WHERE user_id = ? AND is_primary = 1",
            ("user-1",),
        )
        count = (await row.fetchone())[0]
    assert count == 1


@pytest.mark.asyncio
async def test_promotes_existing_session(tmp_config: Config) -> None:
    pre_id = str(uuid4())
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO agent_chat_sessions (id, user_id, title) "
            "VALUES (?, ?, ?)",
            (pre_id, "user-1", "Existing chat"),
        )
        await db.commit()
    session_resolver.clear_cache()

    sid = await session_resolver.get_primary_session_id(tmp_config, "user-1")
    assert sid == pre_id  # promoted, not freshly created

    async with db_session(tmp_config) as db:
        row = await db.execute(
            "SELECT is_primary FROM agent_chat_sessions WHERE id = ?",
            (pre_id,),
        )
        assert (await row.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_different_users_get_different_primaries(tmp_config: Config) -> None:
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-2", "bob", "x", "salt"),
        )
        await db.commit()

    sid1 = await session_resolver.get_primary_session_id(tmp_config, "user-1")
    sid2 = await session_resolver.get_primary_session_id(tmp_config, "user-2")
    assert sid1 != sid2
