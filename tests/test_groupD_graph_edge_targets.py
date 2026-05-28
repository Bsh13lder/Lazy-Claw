"""Integration tests for D1 — get_graph must NOT drop edges whose target note
falls outside the LIMIT window.

Setup:
  - "source" note: high importance (100), inside top-1 window.
  - "low" note:    low importance (1),  outside top-1 window.
  - A note_link from source → low.

Before the fix:  `if to_id in ids` silently drops the edge (and the low node).
After the fix:   the low node is fetched in a second small SELECT; edge renders.

We pass `limit=1` to `get_graph` to force "low" to fall outside the primary
node window even though there are only 2 notes in the DB.
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
            ("user-d1", "alice", "x", "salt-d1"),
        )
        # High-importance source note — will always be in top-1.
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key, importance) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src-note", "user-d1", "enc", "source note", 100),
        )
        # Low-importance target note — falls outside LIMIT 1.
        await db.execute(
            "INSERT INTO notes (id, user_id, content, title_key, importance) "
            "VALUES (?, ?, ?, ?, ?)",
            ("low-note", "user-d1", "enc", "low note", 1),
        )
        # Edge from source → low.
        await db.execute(
            "INSERT INTO note_links (user_id, from_note_id, to_page_name, to_note_id) "
            "VALUES (?, ?, ?, ?)",
            ("user-d1", "src-note", "low note", "low-note"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_edge_target_outside_limit_is_included(tmp_config: Config) -> None:
    """The edge AND the target node must appear even though LIMIT=1 excludes the
    target from the primary importance window."""
    result = await graph.get_graph(tmp_config, "user-d1", limit=1)

    node_ids = {n["id"] for n in result["nodes"]}
    edge_pairs = [(e["source"], e["target"]) for e in result["edges"]]

    # Both nodes must be present.
    assert "src-note" in node_ids, "source note missing from nodes"
    assert "low-note" in node_ids, (
        "low-note should be fetched as secondary edge-target node "
        "even though it falls outside limit=1"
    )

    # The edge must be present.
    assert ("src-note", "low-note") in edge_pairs, (
        "edge src-note→low-note dropped; D1 not fixed"
    )


@pytest.mark.asyncio
async def test_null_to_note_id_edge_still_dropped(tmp_config: Config) -> None:
    """Unresolved edges (to_note_id IS NULL) should still be excluded from the
    rendered graph — only edges with a valid to_note_id are promoted."""
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO note_links (user_id, from_note_id, to_page_name, to_note_id) "
            "VALUES (?, ?, ?, NULL)",
            ("user-d1", "src-note", "orphan page"),
        )
        await db.commit()

    result = await graph.get_graph(tmp_config, "user-d1", limit=1)
    # Only the resolved edge should appear.
    for edge in result["edges"]:
        assert edge["target"] is not None, "NULL-target edge leaked into edges list"
