"""Integration tests for D2 — get_neighbors must respect the same
include_rolled_up / include_archived filters that get_graph applies.

Bug: before the fix, get_neighbors fetches neighbors by id IN (...) with no
tag/archived filter, so clicking a node surfaces hidden rolled-up source notes.

Tests:
  - Default call (include_rolled_up=False):  rolled-up note is NOT in result.
  - Call with include_rolled_up=True:         rolled-up note IS in result.
  - Default call (include_archived=False):    archived note is NOT in result.
  - Call with include_archived=True:          archived note IS in result.
  - Hidden notes do not appear in BFS visited frontier (can't be traversed through).
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
            ("user-d2", "bob", "x", "salt-d2"),
        )
        # Root note — always visible.
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key) "
            "VALUES (?, ?, ?, ?)",
            ("root-note", "user-d2", "enc", "root"),
        )
        # Rolled-up note linked from root.
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            ("rolled-note", "user-d2", "enc", "rolled", '["rolled-up","kind/rollup"]'),
        )
        # Archived note linked from root.
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key, archived) "
            "VALUES (?, ?, ?, ?, ?)",
            ("archived-note", "user-d2", "enc", "archived_note", 1),
        )
        # Visible normal note linked from root.
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key) "
            "VALUES (?, ?, ?, ?)",
            ("visible-note", "user-d2", "enc", "visible"),
        )
        # Links: root → rolled, root → archived, root → visible.
        for target_id, page_name in [
            ("rolled-note", "rolled"),
            ("archived-note", "archived_note"),
            ("visible-note", "visible"),
        ]:
            await db.execute(
                "INSERT INTO note_links (user_id, from_note_id, to_page_name, to_note_id) "
                "VALUES (?, ?, ?, ?)",
                ("user-d2", "root-note", page_name, target_id),
            )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_rolled_up_hidden_by_default(tmp_config: Config) -> None:
    result = await graph.get_neighbors(tmp_config, "user-d2", "root-note")
    node_ids = {n["id"] for n in result["nodes"]}
    assert "rolled-note" not in node_ids, (
        "rolled-up note should be hidden when include_rolled_up=False"
    )
    assert "visible-note" in node_ids, "visible note should always appear"


@pytest.mark.asyncio
async def test_rolled_up_visible_when_flag_set(tmp_config: Config) -> None:
    result = await graph.get_neighbors(
        tmp_config, "user-d2", "root-note", include_rolled_up=True
    )
    node_ids = {n["id"] for n in result["nodes"]}
    assert "rolled-note" in node_ids, (
        "rolled-up note should be visible when include_rolled_up=True"
    )


@pytest.mark.asyncio
async def test_archived_hidden_by_default(tmp_config: Config) -> None:
    result = await graph.get_neighbors(tmp_config, "user-d2", "root-note")
    node_ids = {n["id"] for n in result["nodes"]}
    assert "archived-note" not in node_ids, (
        "archived note should be hidden when include_archived=False"
    )


@pytest.mark.asyncio
async def test_archived_visible_when_flag_set(tmp_config: Config) -> None:
    result = await graph.get_neighbors(
        tmp_config, "user-d2", "root-note", include_archived=True
    )
    node_ids = {n["id"] for n in result["nodes"]}
    assert "archived-note" in node_ids, (
        "archived note should be visible when include_archived=True"
    )


@pytest.mark.asyncio
async def test_hidden_notes_not_traversed_through(tmp_config: Config) -> None:
    """A hidden (rolled-up) note should not act as a BFS hop — notes reachable
    only through a hidden intermediate node should NOT surface in the default
    get_neighbors result."""
    # We need an extra note reachable only via the rolled-up intermediate.
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key) "
            "VALUES (?, ?, ?, ?)",
            ("deep-note", "user-d2", "enc", "deep"),
        )
        await db.execute(
            "INSERT INTO note_links (user_id, from_note_id, to_page_name, to_note_id) "
            "VALUES (?, ?, ?, ?)",
            ("user-d2", "rolled-note", "deep", "deep-note"),
        )
        await db.commit()

    result = await graph.get_neighbors(
        tmp_config, "user-d2", "root-note", depth=2
    )
    node_ids = {n["id"] for n in result["nodes"]}
    assert "deep-note" not in node_ids, (
        "deep-note is only reachable via a rolled-up intermediate; "
        "it must not appear when include_rolled_up=False"
    )
