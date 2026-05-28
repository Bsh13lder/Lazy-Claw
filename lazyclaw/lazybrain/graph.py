"""Graph payload builder — turns ``notes`` + ``note_links`` into the
``{nodes, edges}`` shape the React GraphView expects.

Everything here runs off plaintext columns (``title_key``, ``to_page_name``,
``tags``) so it stays cheap — no per-row decrypt pass.

Phase I — Leiden community detection: when ``python-igraph`` + ``leidenalg``
are installed, every node carries a ``community_id`` (small int) the
frontend can hue-rotate so topic clusters get distinct colors. Falls back
to ``community_id=None`` when the libs are missing — UI then keeps the
existing tag-based palette.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.lazybrain import store
from lazyclaw.lazybrain.wikilinks import normalize_page

logger = logging.getLogger(__name__)

# Lazy-imported community detection. We only build the leidenalg graph
# once per (user_id, layout) within a 60 s window — partition is stable
# given the same edge set, so caching avoids burning CPU on UI panel
# toggles. Set to None when import or partition fails.
_PARTITION_TTL_SECONDS = 60.0
_partition_cache: dict[
    tuple[str, str],
    tuple[float, dict[str, int]],  # (expiry_ts, {node_id: community_id})
] = {}


def _try_leiden(
    cache_key: tuple[str, str],
    node_ids: list[str],
    edges: list[tuple[str, str]],
) -> dict[str, int]:
    """Compute a Leiden partition over the given (nodes, edges).

    Returns ``{node_id: community_id}`` or ``{}`` when leidenalg /
    python-igraph aren't installed or partitioning fails. Result is
    cached for ``_PARTITION_TTL_SECONDS`` per cache_key.
    """
    now = time.monotonic()
    cached = _partition_cache.get(cache_key)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        import igraph  # type: ignore
        import leidenalg  # type: ignore
    except Exception:
        return {}
    try:
        idx_of = {nid: i for i, nid in enumerate(node_ids)}
        e_indexed = [
            (idx_of[a], idx_of[b])
            for a, b in edges
            if a in idx_of and b in idx_of and a != b
        ]
        g = igraph.Graph(n=len(node_ids), edges=e_indexed, directed=False)
        # RBConfigurationVertexPartition with resolution=1.0 is the
        # community-default for "natural" cluster size on sparse PKM graphs.
        partition = leidenalg.find_partition(
            g,
            leidenalg.RBConfigurationVertexPartition,
            resolution_parameter=1.0,
            seed=42,  # deterministic — same vault → same colors
        )
        out: dict[str, int] = {}
        for community_id, members in enumerate(partition):
            for vertex_idx in members:
                if 0 <= vertex_idx < len(node_ids):
                    out[node_ids[vertex_idx]] = community_id
        _partition_cache[cache_key] = (now + _PARTITION_TTL_SECONDS, out)
        return out
    except Exception:
        logger.debug("leiden partition failed", exc_info=True)
        return {}


def _tag_count(raw: Any) -> int:
    """Count tags in a JSON-encoded tag list stored in ``notes.tags``.

    Each tag serialises to two double-quote characters in JSON (opening and
    closing), so ``count('"') // 2`` gives the tag count without a full JSON
    parse.  Returns 0 for NULL or an empty array.

    Examples::

        _tag_count(None)            → 0
        _tag_count("[]")            → 0
        _tag_count('["a"]')         → 1
        _tag_count('["a","b","c"]') → 3
    """
    return (raw or "[]").count('"') // 2


async def get_graph(
    config: Config,
    user_id: str,
    *,
    include_rolled_up: bool = False,
    include_archived: bool = False,
    limit: int = 500,
) -> dict:
    """Return the full user graph (capped at ``limit`` nodes).

    ``include_rolled_up=False`` (default) hides notes that have been folded
    into a weekly/monthly rollup. Rollup notes themselves (which carry
    ``kind/rollup`` but not ``rolled-up``) always render. Pass
    ``include_rolled_up=True`` to surface every original alongside its
    rollup — useful for the "Archive" toggle in the UI.

    ``include_archived=False`` (default) hides notes flagged ``archived=1``
    (skills-vault shapes, archived plans). Pass ``True`` to surface them
    when the user explicitly opens the vault.

    Edges carry ``edge_type`` (``wikilink`` / ``supersedes`` /
    ``contradicts`` / ``references`` / ``derives_from``) and ``source``
    (``auto`` / ``user`` / ``skill_lesson_auto`` / etc.) so the frontend
    can render typed edges distinctly without a second query.
    """
    clauses = ["user_id = ?"]
    params: list = [user_id]
    if not include_rolled_up:
        # IS NULL guard: SQLite "NULL NOT LIKE 'x'" → NULL → treated as
        # FALSE in WHERE, silently dropping every tag-less note.
        clauses.append("(tags IS NULL OR tags NOT LIKE ?)")
        params.append('%"rolled-up"%')
    if not include_archived:
        clauses.append("(archived IS NULL OR archived = 0)")
    where = " AND ".join(clauses)
    async with db_session(config) as db:
        rows = await db.execute(
            f"SELECT id, title_key, pinned, importance, tags, created_at "
            f"FROM notes WHERE {where} "
            f"ORDER BY pinned DESC, importance DESC, created_at DESC LIMIT ?",
            (*params, max(1, min(2000, limit))),
        )
        node_rows = await rows.fetchall()

        ids = {row[0] for row in node_rows}
        if not ids:
            return {"nodes": [], "edges": []}

        placeholders = ",".join("?" * len(ids))
        edge_rows = await db.execute(
            f"SELECT from_note_id, to_note_id, to_page_name, edge_type, source "
            f"FROM note_links WHERE user_id = ? AND from_note_id IN ({placeholders})",
            (user_id, *ids),
        )
        edges_raw = await edge_rows.fetchall()

        # D1 — Secondary fetch: collect all resolved to_note_id values that
        # fall outside the primary LIMIT window. Fetch those rows so an edge
        # to a low-importance note is never silently dropped just because the
        # note didn't make the top-N cut.  The extra set is naturally small
        # (bounded by the number of distinct edge targets).
        resolved_targets = {row[1] for row in edges_raw if row[1]}
        missing_targets = resolved_targets - ids
        if missing_targets:
            # Apply the same include_rolled_up / include_archived filters so
            # hidden notes don't sneak back in through the secondary path.
            sec_clauses = ["user_id = ?"]
            sec_params: list = [user_id]
            sec_ph = ",".join("?" * len(missing_targets))
            sec_clauses.append(f"id IN ({sec_ph})")
            sec_params.extend(missing_targets)
            if not include_rolled_up:
                sec_clauses.append("(tags IS NULL OR tags NOT LIKE ?)")
                sec_params.append('%"rolled-up"%')
            if not include_archived:
                sec_clauses.append("(archived IS NULL OR archived = 0)")
            sec_where = " AND ".join(sec_clauses)
            sec_rows_cur = await db.execute(
                f"SELECT id, title_key, pinned, importance, tags, created_at "
                f"FROM notes WHERE {sec_where}",
                sec_params,
            )
            sec_rows = await sec_rows_cur.fetchall()
            node_rows = list(node_rows) + sec_rows
            ids |= {row[0] for row in sec_rows}

    edges = []
    for from_id, to_id, to_page, edge_type, source in edges_raw:
        if to_id and to_id in ids:
            edges.append({
                "source": from_id,
                "target": to_id,
                "label": to_page,
                "edge_type": edge_type or "wikilink",
                "edge_source": source,
            })
        # Only edges with NULL to_note_id are dropped (unresolved wikilinks —
        # the target page hasn't been created yet). All edges pointing at
        # existing notes are now rendered; the secondary fetch above ensures
        # their targets are in the node list regardless of the LIMIT window.

    # Phase I — Leiden community partition. Cache key disambiguates by the
    # filter mode so "default", "include_rolled_up", and "include_archived"
    # views don't trample each other.
    cache_mode = (
        f"r{int(include_rolled_up)}a{int(include_archived)}"
    )
    community_by_id = _try_leiden(
        (user_id, cache_mode),
        node_ids=[row[0] for row in node_rows],
        edges=[(e["source"], e["target"]) for e in edges],
    )

    nodes = [
        {
            "id": row[0],
            "label": row[1] or row[0][:8],
            "pinned": bool(row[2]),
            "importance": row[3],
            "tag_count": _tag_count(row[4]),
            "community_id": community_by_id.get(row[0]),
        }
        for row in node_rows
    ]

    return {"nodes": nodes, "edges": edges}


async def get_neighbors(
    config: Config,
    user_id: str,
    note_id: str,
    *,
    depth: int = 1,
    include_rolled_up: bool = False,
    include_archived: bool = False,
) -> dict:
    """BFS out from ``note_id`` up to ``depth`` hops.

    Returns the same ``{nodes, edges}`` shape as :func:`get_graph`.

    ``include_rolled_up`` and ``include_archived`` mirror the identically-named
    parameters on :func:`get_graph`:

    * ``include_rolled_up=False`` (default) — notes tagged ``rolled-up`` are
      excluded from both the result set and the BFS traversal frontier, so they
      can't act as hidden hops to deeper nodes.
    * ``include_archived=False`` (default) — notes with ``archived=1`` are
      similarly excluded.

    Pass ``True`` for either flag to surface hidden notes when the caller
    explicitly requests the full neighbourhood (e.g. the vault / archive view).
    """
    depth = max(1, min(3, depth))

    # Build the tag/archived filter fragment used to gate which notes are
    # allowed into the BFS visited set.  Reused in multiple queries below.
    filter_clauses: list[str] = []
    if not include_rolled_up:
        filter_clauses.append("(tags IS NULL OR tags NOT LIKE '%\"rolled-up\"%')")
    if not include_archived:
        filter_clauses.append("(archived IS NULL OR archived = 0)")
    filter_sql = (" AND " + " AND ".join(filter_clauses)) if filter_clauses else ""

    visited: set[str] = {note_id}
    frontier: set[str] = {note_id}

    async with db_session(config) as db:
        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            # Outbound: links whose from_note_id is in the frontier.
            # Only follow edges whose target passes the visibility filter —
            # hidden notes must not enter `visited` and can't be traversed.
            rows_out = await db.execute(
                f"SELECT DISTINCT nl.to_note_id FROM note_links nl "
                f"JOIN notes n ON n.id = nl.to_note_id "
                f"WHERE nl.user_id = ? AND nl.from_note_id IN ({placeholders}) "
                f"AND nl.to_note_id IS NOT NULL"
                f"{filter_sql.replace('tags', 'n.tags').replace('archived', 'n.archived')}",
                (user_id, *frontier),
            )
            out_ids = {r[0] for r in await rows_out.fetchall() if r[0]}
            # Inbound: links pointing at the frontier, from notes that also
            # pass the visibility filter (hidden source notes stay invisible).
            rows_in = await db.execute(
                f"SELECT DISTINCT nl.from_note_id FROM note_links nl "
                f"JOIN notes n ON n.id = nl.from_note_id "
                f"WHERE nl.user_id = ? AND nl.to_note_id IN ({placeholders})"
                f"{filter_sql.replace('tags', 'n.tags').replace('archived', 'n.archived')}",
                (user_id, *frontier),
            )
            in_ids = {r[0] for r in await rows_in.fetchall()}
            next_frontier = (out_ids | in_ids) - visited
            visited |= next_frontier
            frontier = next_frontier

    if not visited:
        return {"nodes": [], "edges": []}

    # Fetch node metadata for everything in `visited`, applying the same
    # visibility filter so hidden notes don't appear even if they ended up
    # in `visited` via the seed `note_id` itself.
    placeholders = ",".join("?" * len(visited))
    vis_clauses = [f"user_id = ?", f"id IN ({placeholders})"]
    vis_params: list = [user_id, *visited]
    if not include_rolled_up:
        vis_clauses.append("(tags IS NULL OR tags NOT LIKE ?)")
        vis_params.append('%"rolled-up"%')
    if not include_archived:
        vis_clauses.append("(archived IS NULL OR archived = 0)")
    vis_where = " AND ".join(vis_clauses)

    async with db_session(config) as db:
        node_rows_cur = await db.execute(
            f"SELECT id, title_key, pinned, importance "
            f"FROM notes WHERE {vis_where}",
            vis_params,
        )
        nodes_raw = await node_rows_cur.fetchall()
        edge_rows_cur = await db.execute(
            f"SELECT from_note_id, to_note_id, to_page_name, edge_type, source "
            f"FROM note_links WHERE user_id = ? AND from_note_id IN ({placeholders})",
            (user_id, *visited),
        )
        edges_raw = await edge_rows_cur.fetchall()

    visible_ids = {row[0] for row in nodes_raw}
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
        {
            "source": from_id,
            "target": to_id,
            "label": page,
            "edge_type": edge_type or "wikilink",
            "edge_source": edge_source,
        }
        for from_id, to_id, page, edge_type, edge_source in edges_raw
        if to_id and to_id in visible_ids
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
