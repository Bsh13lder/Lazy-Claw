"""Graph-position upsert — cross-device layout memory.

Guarantees:
  1. ``get_positions`` on an empty table returns an empty dict.
  2. ``save_positions`` writes new rows; ``get_positions`` reads them back.
  3. A second save upserts (updates x/y, doesn't duplicate rows).
  4. Unknown layout modes raise ValueError (the table is not a general
     key-value store for any caller-supplied string).
  5. Positions for a note owned by a different user are silently dropped,
     not inserted — so one user can't plant coords on another's notes.
  6. Deleting the note cascades to the position row.
  7. NaN / ±Inf coordinates are rejected, never stored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import graph


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-1", "alice", "x", "salt-a"),
        )
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("user-2", "bob", "x", "salt-b"),
        )
        # Seed some notes for user-1, one for user-2.
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key) "
            "VALUES (?, ?, ?, ?)",
            ("note-a", "user-1", "ciphertext", "alpha"),
        )
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key) "
            "VALUES (?, ?, ?, ?)",
            ("note-b", "user-1", "ciphertext", "beta"),
        )
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key) "
            "VALUES (?, ?, ?, ?)",
            ("note-c", "user-2", "ciphertext", "gamma"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_empty_table_returns_empty_dict(tmp_config: Config) -> None:
    result = await graph.get_positions(tmp_config, "user-1", "neural-link")
    assert result == {}


@pytest.mark.asyncio
async def test_save_then_load_round_trip(tmp_config: Config) -> None:
    written = await graph.save_positions(
        tmp_config,
        "user-1",
        "neural-link",
        {"note-a": (123.5, -45.25), "note-b": (0.0, 0.0)},
    )
    assert written == 2

    loaded = await graph.get_positions(tmp_config, "user-1", "neural-link")
    assert loaded == {"note-a": (123.5, -45.25), "note-b": (0.0, 0.0)}


@pytest.mark.asyncio
async def test_upsert_updates_existing_row(tmp_config: Config) -> None:
    await graph.save_positions(
        tmp_config, "user-1", "neural-link", {"note-a": (1.0, 2.0)}
    )
    await graph.save_positions(
        tmp_config, "user-1", "neural-link", {"note-a": (9.0, 9.0)}
    )
    loaded = await graph.get_positions(tmp_config, "user-1", "neural-link")
    assert loaded == {"note-a": (9.0, 9.0)}

    # One row, not two.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT COUNT(*) FROM note_layout_positions WHERE note_id = 'note-a'"
        )
        (count,) = await rows.fetchone()
    assert count == 1


@pytest.mark.asyncio
async def test_modes_are_independent(tmp_config: Config) -> None:
    await graph.save_positions(
        tmp_config, "user-1", "neural-link", {"note-a": (10.0, 20.0)}
    )
    await graph.save_positions(
        tmp_config, "user-1", "category", {"note-a": (99.0, 99.0)}
    )

    neural = await graph.get_positions(tmp_config, "user-1", "neural-link")
    category = await graph.get_positions(tmp_config, "user-1", "category")
    assert neural == {"note-a": (10.0, 20.0)}
    assert category == {"note-a": (99.0, 99.0)}


@pytest.mark.asyncio
async def test_rejects_unknown_mode(tmp_config: Config) -> None:
    with pytest.raises(ValueError):
        await graph.save_positions(
            tmp_config, "user-1", "canvas", {"note-a": (0.0, 0.0)}
        )
    with pytest.raises(ValueError):
        await graph.get_positions(tmp_config, "user-1", "freeform")


@pytest.mark.asyncio
async def test_other_users_notes_silently_dropped(tmp_config: Config) -> None:
    # user-1 tries to plant positions on note-c (which belongs to user-2).
    written = await graph.save_positions(
        tmp_config,
        "user-1",
        "neural-link",
        {"note-a": (1.0, 2.0), "note-c": (666.0, 666.0)},
    )
    # Only note-a passes the ownership filter.
    assert written == 1
    loaded = await graph.get_positions(tmp_config, "user-1", "neural-link")
    assert "note-c" not in loaded
    # And user-2 definitely didn't magically gain the row either.
    loaded_u2 = await graph.get_positions(tmp_config, "user-2", "neural-link")
    assert loaded_u2 == {}


@pytest.mark.asyncio
async def test_note_delete_cascades(tmp_config: Config) -> None:
    await graph.save_positions(
        tmp_config, "user-1", "neural-link", {"note-a": (1.0, 1.0)}
    )
    async with db_session(tmp_config) as db:
        # SQLite needs PRAGMA foreign_keys = ON for ON DELETE CASCADE to fire.
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("DELETE FROM notes WHERE id = 'note-a'")
        await db.commit()

    remaining = await graph.get_positions(tmp_config, "user-1", "neural-link")
    assert remaining == {}


@pytest.mark.asyncio
async def test_rejects_non_finite_coords(tmp_config: Config) -> None:
    written = await graph.save_positions(
        tmp_config,
        "user-1",
        "neural-link",
        {
            "note-a": (float("nan"), 1.0),
            "note-b": (float("inf"), 2.0),
        },
    )
    assert written == 0
    loaded = await graph.get_positions(tmp_config, "user-1", "neural-link")
    assert loaded == {}
