"""One-shot LazyBrain graph cleanup (2026-05-29).

Removes test-period pollution while PRESERVING skills (kind/shape) and
durable typed memories (user/feedback/project/reference). Backs up the DB
(online backup) before deleting anything. Dry-run by default; set APPLY=1
to actually delete.

Run INSIDE the lazyclaw container so SQLite WAL locking stays coherent:
    docker exec [-e APPLY=1] -e TS=<utc-stamp> lazyclaw python3 /app/scripts/graph_cleanup_2026_05_29.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, get_db_path
from lazyclaw.lazybrain import store

CUT = "2026-05-15"  # keep on/after this (last ~2 weeks)
TEST_USER = "u-test"
SKILL_TAG = "kind/shape"
DURABLE_TYPES = {"user", "feedback", "project", "reference"}
CLAUDE_PLAN_TAGS = {"source/claude-plan", "kind/plan"}
# Old agent-fact notes carrying any of these are PRESERVED (conservative).
PROTECTED_TAGS = {
    "lesson", "command", "recipe", "contact",
    "kind/rollup", "browser-template", "learned_preference",
}


def _tags_of(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(t) for t in v] if isinstance(v, list) else []
    except Exception:
        return []


def _classify(row: sqlite3.Row) -> str | None:
    """Return deletion reason, or None to KEEP."""
    user_id = row["user_id"]
    tags = _tags_of(row["tags"])
    mt = row["memory_type"]
    created = row["created_at"] or ""
    tagset = set(tags)

    # These OVERRIDE preservation — pollution regardless of type/skill flag.
    # Cross-project Claude Code plan leak — forbidden by CLAUDE.md, any age.
    if tagset & CLAUDE_PLAN_TAGS:
        return "claude-plan-leak"
    # Test user — pure scratch data, any age.
    if user_id == TEST_USER:
        return "test-user"

    # Always preserve skills + durable curated memories.
    if SKILL_TAG in tagset:
        return None
    if mt in DURABLE_TYPES:
        return None

    is_old = created < CUT
    if not is_old:
        return None

    if mt == "session-log":
        return "old-session-log"
    if mt in (None, "", "other"):
        return "old-null/other"
    if mt == "fact" and "owner/agent" in tagset:
        if tagset & PROTECTED_TAGS:
            return None
        if any(t.startswith("template/") for t in tags):
            return None
        return "old-agent-fact"
    return None


async def main() -> None:
    apply = os.environ.get("APPLY") == "1"
    cfg = Config()
    db_path = str(get_db_path(cfg))

    # Gather all rows (read-only).
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT id, user_id, tags, memory_type, created_at FROM notes"
        )
        rows = await cur.fetchall()

    total = len(rows)
    targets: dict[str, list[str]] = {}  # reason -> [id]
    keep_skill = keep_durable = 0
    for r in rows:
        tagset = set(_tags_of(r["tags"]))
        if SKILL_TAG in tagset:
            keep_skill += 1
        elif r["memory_type"] in DURABLE_TYPES:
            keep_durable += 1
        reason = _classify(r)
        if reason:
            targets.setdefault(reason, []).append(r["id"])

    all_ids = [i for ids in targets.values() for i in ids]
    print(f"total notes: {total}")
    print(f"PRESERVE skills (kind/shape): {keep_skill}")
    print(f"PRESERVE durable (user/feedback/project/reference): {keep_durable}")
    print("DELETE breakdown:")
    for reason, ids in sorted(targets.items()):
        print(f"  {reason}: {len(ids)}")
    print(f"DELETE total: {len(all_ids)}")
    print(f"SURVIVORS after delete: {total - len(all_ids)}")

    if not apply:
        print("\nDRY-RUN (set APPLY=1 to execute). Nothing changed.")
        await close_pool()
        return

    # ── BACKUP FIRST (online backup; abort on any failure) ──────────────
    ts = os.environ.get("TS") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{os.path.dirname(db_path)}/backups/lazyclaw.pre-graph-cleanup.{ts}.db"
    print(f"\nBacking up -> {backup_path}")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        with dst:
            src.backup(dst)
        # Integrity + count check on the backup.
        ic = dst.execute("PRAGMA integrity_check").fetchone()[0]
        bcount = dst.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    finally:
        src.close()
        dst.close()
    print(f"backup integrity_check: {ic}; backup notes: {bcount}")
    if ic != "ok" or bcount != total:
        print("BACKUP VERIFICATION FAILED — ABORTING, nothing deleted.")
        await close_pool()
        return

    # ── DELETE via the app's own delete_note (cleans FTS/links) ─────────
    # Map id -> user_id so delete_note scopes correctly.
    uid_by_id = {r["id"]: r["user_id"] for r in rows}
    deleted = 0
    for nid in all_ids:
        ok = await store.delete_note(cfg, uid_by_id[nid], nid)
        if ok:
            deleted += 1
    print(f"deleted: {deleted}/{len(all_ids)}")

    async with db_session(cfg) as db:
        cur = await db.execute("SELECT COUNT(*) FROM notes")
        remaining = (await cur.fetchone())[0]
    print(f"remaining notes: {remaining}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
