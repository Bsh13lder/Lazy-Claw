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


# ---------------------------------------------------------------------------
# Graph node positions — per-user, per-layout-mode persistence so the
# neural-link graph opens where the user left it. Plaintext x/y; never
# encrypted since coordinates leak nothing.
# ---------------------------------------------------------------------------

# Accept only the layouts the React GraphView actually uses. Rejecting
# anything else at this layer keeps the table from being turned into a
# general-purpose key-value store by a misbehaving client.
_ALLOWED_MODES = frozenset({"category", "neural-link"})


def _validate_mode(mode: str) -> None:
    if mode not in _ALLOWED_MODES:
        raise ValueError(f"Unsupported layout mode: {mode!r}")


async def get_positions(
    config: Config, user_id: str, mode: str
) -> dict[str, tuple[float, float]]:
    """Load saved {note_id: (x, y)} for one user+mode. Empty dict when none."""
    _validate_mode(mode)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT note_id, x, y FROM note_layout_positions "
            "WHERE user_id = ? AND mode = ?",
            (user_id, mode),
        )
        return {r[0]: (float(r[1]), float(r[2])) for r in await rows.fetchall()}


async def save_positions(
    config: Config,
    user_id: str,
    mode: str,
    positions: dict[str, tuple[float, float]],
) -> int:
    """Upsert a batch of note positions. Returns the number of rows written.

    - Silently drops note_ids that don't belong to this user (defence in
      depth — the ON DELETE CASCADE would never fire for them anyway).
    - Finite-value check on every coord; rejects NaN / ±Inf so the table
      can't be poisoned by a buggy client.
    """
    _validate_mode(mode)
    if not positions:
        return 0

    rows: list[tuple[str, str, str, float, float]] = []
    for note_id, (x, y) in positions.items():
        if not (
            isinstance(x, (int, float))
            and isinstance(y, (int, float))
            and x == x  # reject NaN
            and y == y
            and x != float("inf") and x != float("-inf")
            and y != float("inf") and y != float("-inf")
        ):
            continue
        rows.append((user_id, mode, note_id, float(x), float(y)))

    if not rows:
        return 0

    async with db_session(config) as db:
        # Scope the upsert to notes the caller actually owns. This is cheaper
        # than a per-row foreign-key check and gives an atomic "reject rows
        # you shouldn't touch" semantics.
        note_ids = {r[2] for r in rows}
        placeholders = ",".join("?" * len(note_ids))
        owned_rows = await db.execute(
            f"SELECT id FROM notes WHERE user_id = ? AND id IN ({placeholders})",
            (user_id, *note_ids),
        )
        owned = {r[0] for r in await owned_rows.fetchall()}
        rows = [r for r in rows if r[2] in owned]
        if not rows:
            return 0

        await db.executemany(
            "INSERT INTO note_layout_positions "
            "(user_id, mode, note_id, x, y, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(user_id, mode, note_id) DO UPDATE SET "
            "x = excluded.x, y = excluded.y, updated_at = excluded.updated_at",
            rows,
        )
        await db.commit()
    return len(rows)
