"""Phase 0 backend profile for the LazyBrain graph endpoint.

Two modes:
  - ``db``  — open ``data/lazyclaw.db`` directly and time the SQL that
              ``lazyclaw/lazybrain/graph.py::get_graph`` runs (pure DB latency).
  - ``http`` — hit ``GET /api/lazybrain/graph?limit=500`` via the running
              server (end-to-end latency including auth + JSON serialize).

Usage
-----
    python scripts/profile_graph_endpoint.py db --user-id YOUR_ID --n 50
    python scripts/profile_graph_endpoint.py http --session-id YOUR_COOKIE --n 50

The DB mode is the authoritative number — it tells us whether the DB itself
is the bottleneck. The HTTP mode measures round-trip from inside the same
host; useful to spot FastAPI/serialization overhead.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import aiosqlite
import httpx


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "lazyclaw.db"


async def profile_db(db_path: Path, user_id: str, n: int) -> list[float]:
    """Run the exact graph SELECT N times and return per-call ms."""
    durations: list[float] = []
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")

        # Warm-up (fill page cache) — don't count this one.
        await _run_graph_queries(db, user_id, limit=500)

        for _ in range(n):
            t0 = time.perf_counter()
            await _run_graph_queries(db, user_id, limit=500)
            durations.append((time.perf_counter() - t0) * 1000.0)

        await _explain_query_plan(db, user_id)

    return durations


async def _run_graph_queries(
    db: aiosqlite.Connection, user_id: str, *, limit: int
) -> None:
    """Mirror the two queries in lazybrain.graph.get_graph."""
    rows = await db.execute(
        "SELECT id, title_key, pinned, importance, tags, created_at "
        "FROM notes WHERE user_id = ? "
        "ORDER BY pinned DESC, importance DESC, created_at DESC LIMIT ?",
        (user_id, limit),
    )
    node_rows = await rows.fetchall()
    ids = {row[0] for row in node_rows}
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    edge_rows = await db.execute(
        f"SELECT from_note_id, to_note_id, to_page_name "
        f"FROM note_links WHERE user_id = ? AND from_note_id IN ({placeholders})",
        (user_id, *ids),
    )
    await edge_rows.fetchall()


async def _explain_query_plan(db: aiosqlite.Connection, user_id: str) -> None:
    """Dump EXPLAIN QUERY PLAN so we can confirm the index is hit."""
    print("\n--- EXPLAIN QUERY PLAN (notes select) ---")
    rows = await db.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT id, title_key, pinned, importance, tags, created_at "
        "FROM notes WHERE user_id = ? "
        "ORDER BY pinned DESC, importance DESC, created_at DESC LIMIT 500",
        (user_id,),
    )
    for row in await rows.fetchall():
        print(" ", " | ".join(str(c) for c in row))


async def profile_http(
    base_url: str, session_id: str, n: int
) -> list[float]:
    durations: list[float] = []
    cookies = {"session_id": session_id}
    async with httpx.AsyncClient(
        base_url=base_url, cookies=cookies, timeout=30.0
    ) as client:
        # Warm-up
        r = await client.get("/api/lazybrain/graph", params={"limit": 500})
        r.raise_for_status()

        for _ in range(n):
            t0 = time.perf_counter()
            r = await client.get("/api/lazybrain/graph", params={"limit": 500})
            r.raise_for_status()
            durations.append((time.perf_counter() - t0) * 1000.0)

    return durations


def report(durations: list[float], label: str) -> None:
    durations_sorted = sorted(durations)
    n = len(durations_sorted)

    def pct(p: float) -> float:
        if not durations_sorted:
            return 0.0
        k = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
        return durations_sorted[k]

    print(f"\n=== {label} (n={n}) ===")
    print(f"  min    {min(durations_sorted):7.2f} ms")
    print(f"  p50    {statistics.median(durations_sorted):7.2f} ms")
    print(f"  p95    {pct(95):7.2f} ms")
    print(f"  p99    {pct(99):7.2f} ms")
    print(f"  max    {max(durations_sorted):7.2f} ms")
    print(f"  mean   {statistics.mean(durations_sorted):7.2f} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    db = sub.add_parser("db", help="Profile raw SQL against the sqlite file")
    db.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    db.add_argument("--user-id", required=True)
    db.add_argument("--n", type=int, default=50)

    http = sub.add_parser("http", help="Profile the HTTP endpoint")
    http.add_argument("--base-url", default="http://localhost:18789")
    http.add_argument("--session-id", required=True)
    http.add_argument("--n", type=int, default=50)

    args = parser.parse_args()

    if args.mode == "db":
        if not args.db_path.exists():
            print(f"DB not found: {args.db_path}", file=sys.stderr)
            return 2
        durations = asyncio.run(
            profile_db(args.db_path, args.user_id, args.n)
        )
        report(durations, f"DB direct ({args.db_path.name})")
    else:
        durations = asyncio.run(
            profile_http(args.base_url, args.session_id, args.n)
        )
        report(durations, f"HTTP {args.base_url}/api/lazybrain/graph")

    print("\nTip: on the running container, also capture")
    print("  docker stats --no-stream lazyclaw lazyclaw-web n8n")
    print("while the Neural-links graph is rendered in the browser.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
