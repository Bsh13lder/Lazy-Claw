"""Graph payload builder — turns ``notes`` + ``note_links`` into the
``{nodes, edges}`` shape the React GraphView expects.

Everything here runs off plaintext columns (``title_key``, ``to_page_name``,
``tags``) so it stays cheap — no per-row decrypt pass.
"""
from __future__ import annotations

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.lazybrain import store
from lazyclaw.lazybrain.wikilinks import normalize_page


async def get_graph(
    config: Config,
    user_id: str,
    *,
    limit: int = 500,
) -> dict:
    """Return the full user graph (capped at ``limit`` nodes)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, title_key, pinned, importance, tags, created_at "
            "FROM notes WHERE user_id = ? "
            "ORDER BY pinned DESC, importance DESC, created_at DESC LIMIT ?",
            (user_id, max(1, min(2000, limit))),
        )
        node_rows = await rows.fetchall()

        ids = {row[0] for row in node_rows}
        if not ids:
            return {"nodes": [], "edges": []}

        placeholders = ",".join("?" * len(ids))
        edge_rows = await db.execute(
            f"SELECT from_note_id, to_note_id, to_page_name "
            f"FROM note_links WHERE user_id = ? AND from_note_id IN ({placeholders})",
            (user_id, *ids),
        )
        edges_raw = await edge_rows.fetchall()

    nodes = [
        {
            "id": row[0],
            "label": row[1] or row[0][:8],
            "pinned": bool(row[2]),
            "importance": row[3],
            "tag_count": len((row[4] or "[]").count('"') // 2 and row[4] or "[]"),
        }
        for row in node_rows
    ]

    edges = []
    for from_id, to_id, to_page in edges_raw:
        if to_id and to_id in ids:
            edges.append({"source": from_id, "target": to_id, "label": to_page})
        # Unresolved edges (no target note yet) are dropped from the graph
        # view — they surface instead in the backlinks panel of the orphan.

    return {"nodes": nodes, "edges": edges}


async def get_neighbors(
    config: Config,
    user_id: str,
    note_id: str,
    *,
    depth: int = 1,
) -> dict:
    """BFS out from ``note_id`` up to ``depth`` hops. Returns same shape as get_graph."""
    depth = max(1, min(3, depth))
    visited: set[str] = {note_id}
    frontier: set[str] = {note_id}

    async with db_session(config) as db:
        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            # Outbound: links whose from_note_id is in the frontier
            rows_out = await db.execute(
                f"SELECT DISTINCT to_note_id FROM note_links "
                f"WHERE user_id = ? AND from_note_id IN ({placeholders}) "
                f"AND to_note_id IS NOT NULL",
                (user_id, *frontier),
            )
            out_ids = {r[0] for r in await rows_out.fetchall() if r[0]}
            # Inbound: links pointing at the frontier
            rows_in = await db.execute(
                f"SELECT DISTINCT from_note_id FROM note_links "
                f"WHERE user_id = ? AND to_note_id IN ({placeholders})",
                (user_id, *frontier),
            )
            in_ids = {r[0] for r in await rows_in.fetchall()}
            next_frontier = (out_ids | in_ids) - visited
            visited |= next_frontier
            frontier = next_frontier

    if not visited:
        return {"nodes": [], "edges": []}

    # Fetch node metadata for everything in `visited`
    placeholders = ",".join("?" * len(visited))
    async with db_session(config) as db:
        node_rows = await db.execute(
            f"SELECT id, title_key, pinned, importance "
            f"FROM notes WHERE user_id = ? AND id IN ({placeholders})",
            (user_id, *visited),
        )
        nodes_raw = await node_rows.fetchall()
        edge_rows = await db.execute(
            f"SELECT from_note_id, to_note_id, to_page_name "
            f"FROM note_links WHERE user_id = ? AND from_note_id IN ({placeholders})",
            (user_id, *visited),
        )
        edges_raw = await edge_rows.fetchall()

    nodes = [
        {
            "id": row[0],
            "label": row[1] or row[0][:8],
            "pinned": bool(row[2]),
            "importance": row[3],
            "is_root": row[0] == note_id,
        }
        for row in nodes_raw
    ]
    edges = [
        {"source": from_id, "target": to_id, "label": page}
        for from_id, to_id, page in edges_raw
        if to_id and to_id in visited
    ]
    return {"nodes": nodes, "edges": edges}


async def find_linked(
    config: Config, user_id: str, page_name: str
) -> list[dict]:
    """Shortcut: backlinks to a named page (what Logseq calls 'linked references')."""
    return await store.get_backlinks(config, user_id, normalize_page(page_name))
