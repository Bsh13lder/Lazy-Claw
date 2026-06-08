"""One-shot LazyBrain duplicate-note collapse (2026-06-01).

Collapses the duplicate notes left behind by the OLDER auto-capture / lesson
mirror paths (before they were made content-addressable):

  * EXACT duplicates  — notes sharing the same ``title_key``.
  * NEAR-DUP clusters  — agent-owned ``fact`` notes whose ``title_key`` shares
    a 40-char prefix (e.g. ``research · task #1: …`` variants).

For each group it KEEPS THE NEWEST survivor (max ``created_at``; ties broken
by pinned, then importance, then a stable id sort) and deletes the rest via
``store.delete_note`` (which sweeps FTS + embeddings + vec0 + chunks +
note_links both directions — FK CASCADE is OFF in this deployment).

SAFETY (mirrors ``scripts/graph_cleanup_2026_05_29.py``):
  * DRY-RUN by default. Set ``APPLY=1`` to actually delete.
  * Makes an online SQLite backup + integrity/count verify BEFORE any delete.
  * NEVER deletes a note that is pinned, importance >= 8, or whose
    ``memory_type`` is ``user`` / ``feedback`` (durable curated memory).
  * Re-points inbound ``note_links`` from a doomed duplicate onto the
    survivor BEFORE deleting, so backlinks aren't orphaned.

Run INSIDE the lazyclaw container (or with the project venv) so SQLite WAL
locking stays coherent:
    [APPLY=1] [TS=<utc-stamp>] python3 scripts/graph_dedup_2026_06_01.py
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

# Notes carrying these memory_types are NEVER deleted (durable curated memory).
PROTECTED_TYPES = {"user", "feedback"}
# Notes at/above this importance are NEVER deleted.
PROTECTED_IMPORTANCE = 8
# Near-dup clustering: agent-owned fact notes whose title_key shares this many
# leading chars are treated as one near-dup cluster.
NEAR_DUP_PREFIX = 40


def _tags_of(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(t) for t in v] if isinstance(v, list) else []
    except Exception:
        return []


def _is_protected(row: dict) -> bool:
    """True if this note must never be deleted (it may still be a survivor)."""
    if row.get("pinned"):
        return True
    try:
        if int(row.get("importance") or 0) >= PROTECTED_IMPORTANCE:
            return True
    except (TypeError, ValueError):
        pass
    if str(row.get("memory_type") or "") in PROTECTED_TYPES:
        return True
    return False


def _sort_keep_newest(row: dict) -> tuple:
    """Sort key — LARGEST tuple wins the survivor slot.

    Priority: newest created_at, then pinned, then importance, then a stable
    id tiebreak so the choice is deterministic across runs.
    """
    return (
        str(row.get("created_at") or ""),
        1 if row.get("pinned") else 0,
        int(row.get("importance") or 0),
        str(row.get("id") or ""),
    )


def select_survivor(members: list[dict]) -> tuple[str | None, list[str]]:
    """Pure: pick the survivor id and the ids to delete from a dup group.

    KEEP THE NEWEST (``_sort_keep_newest``). Protected members (pinned /
    importance>=8 / durable type) are NEVER in the delete list — even if a
    newer non-protected sibling becomes the survivor. Returns
    ``(survivor_id, [delete_ids])``. With < 2 members nothing is deleted.
    """
    if not members or len(members) < 2:
        return (members[0]["id"] if members else None, [])

    ordered = sorted(members, key=_sort_keep_newest, reverse=True)
    survivor = ordered[0]
    delete_ids = [
        m["id"] for m in ordered[1:] if not _is_protected(m)
    ]
    return (survivor["id"], delete_ids)


def _build_groups(rows: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """Bucket rows into dedup groups, per category.

    Returns ``{category: {group_key: [rows]}}`` for:
      * ``exact_title_key``    — every group with >1 member sharing title_key.
      * ``near_dup_cluster``   — agent-owned fact notes sharing title_key[:40]
        (excludes rows already collapsed by the exact pass to avoid double
        counting).
    """
    by_title_key: dict[str, list[dict]] = {}
    for r in rows:
        tk = r.get("title_key")
        if tk:
            by_title_key.setdefault(tk, []).append(r)

    exact: dict[str, list[dict]] = {
        tk: members for tk, members in by_title_key.items() if len(members) > 1
    }

    # Ids already accounted for in an exact group — don't re-collapse them in
    # the looser near-dup pass.
    exact_ids = {m["id"] for members in exact.values() for m in members}

    near: dict[str, list[dict]] = {}
    for r in rows:
        if r["id"] in exact_ids:
            continue
        tk = r.get("title_key")
        if not tk:
            continue
        tags = set(_tags_of(r.get("tags_raw")))
        is_agent_fact = (
            str(r.get("memory_type") or "") == "fact" and "owner/agent" in tags
        ) or "auto" in tags
        if not is_agent_fact:
            continue
        near.setdefault(tk[:NEAR_DUP_PREFIX], []).append(r)
    near = {k: v for k, v in near.items() if len(v) > 1}

    return {"exact_title_key": exact, "near_dup_cluster": near}


def _detect_real_user(rows: list[dict]) -> str | None:
    """The user_id owning the most notes — the real account. Returns None if
    no rows (then the caller scopes to all users)."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["user_id"]] = counts.get(r["user_id"], 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda u: counts[u])


async def _repoint_inbound_links(
    cfg: Config, user_id: str, dead_id: str, survivor_id: str, survivor_key: str | None
) -> None:
    """Move inbound ``note_links`` pointing at ``dead_id`` onto the survivor
    BEFORE the dead note is deleted, so backlinks don't dangle. Best-effort.
    """
    try:
        async with db_session(cfg) as db:
            await db.execute(
                "UPDATE note_links SET to_note_id = ?, to_page_name = ? "
                "WHERE user_id = ? AND to_note_id = ?",
                (survivor_id, survivor_key or "", user_id, dead_id),
            )
            await db.commit()
    except Exception as e:  # never block the collapse on a link-repoint slip
        print(f"  warn: link repoint failed for {dead_id[:8]}: {e}")


async def main() -> None:
    apply = os.environ.get("APPLY") == "1"
    cfg = Config()
    db_path = str(get_db_path(cfg))

    # Gather all rows read-only (plaintext columns only — no decrypt).
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT id, user_id, title_key, tags, memory_type, created_at, "
            "importance, pinned FROM notes"
        )
        raw_rows = await cur.fetchall()

    rows: list[dict] = [
        {
            "id": r[0],
            "user_id": r[1],
            "title_key": r[2],
            "tags_raw": r[3],
            "memory_type": r[4],
            "created_at": r[5],
            "importance": r[6],
            "pinned": bool(r[7]),
        }
        for r in raw_rows
    ]
    total = len(rows)

    real_user = _detect_real_user(rows)
    if real_user:
        scoped = [r for r in rows if r["user_id"] == real_user]
        scope_desc = f"real user {real_user} ({len(scoped)} notes)"
    else:
        scoped = rows
        scope_desc = "ALL users"

    groups = _build_groups(scoped)

    # Plan deletes per category, honouring protection + survivor selection.
    plan: dict[str, list[tuple[str, str, str | None]]] = {}  # cat -> [(dead, survivor, survivor_key)]
    survivor_keys = {r["id"]: r["title_key"] for r in scoped}
    for category, gmap in groups.items():
        actions: list[tuple[str, str, str | None]] = []
        for _gkey, members in gmap.items():
            survivor_id, delete_ids = select_survivor(members)
            if not survivor_id or not delete_ids:
                continue
            for dead in delete_ids:
                actions.append((dead, survivor_id, survivor_keys.get(survivor_id)))
        plan[category] = actions

    print(f"total notes (all users): {total}")
    print(f"scope: {scope_desc}")
    print("WOULD DELETE breakdown:")
    grand = 0
    for category in ("exact_title_key", "near_dup_cluster"):
        n = len(plan.get(category, []))
        grand += n
        print(f"  {category}: {n}  (groups: {len(groups[category])})")
    print(f"WOULD DELETE total: {grand}")
    print(f"PROTECTED (pinned / importance>={PROTECTED_IMPORTANCE} / "
          f"type in {sorted(PROTECTED_TYPES)}) are never deleted.")
    print(f"SURVIVORS after collapse (scoped): {len(scoped) - grand}")

    if not apply:
        print("\nDRY-RUN (set APPLY=1 to execute). Nothing changed.")
        await close_pool()
        return

    # ── BACKUP FIRST (online backup; abort on any failure) ──────────────
    ts = os.environ.get("TS") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = f"{os.path.dirname(db_path)}/backups"
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = f"{backup_dir}/lazyclaw.pre-graph-dedup.{ts}.db"
    print(f"\nBacking up -> {backup_path}")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        with dst:
            src.backup(dst)
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

    # ── COLLAPSE: repoint inbound links, then delete the dup ────────────
    uid_by_id = {r["id"]: r["user_id"] for r in scoped}
    deleted = 0
    attempted = 0
    for category, actions in plan.items():
        for dead, survivor_id, survivor_key in actions:
            attempted += 1
            uid = uid_by_id.get(dead)
            if not uid:
                continue
            await _repoint_inbound_links(cfg, uid, dead, survivor_id, survivor_key)
            ok = await store.delete_note(cfg, uid, dead)
            if ok:
                deleted += 1
    print(f"deleted: {deleted}/{attempted}")

    async with db_session(cfg) as db:
        cur = await db.execute("SELECT COUNT(*) FROM notes")
        remaining = (await cur.fetchone())[0]
    print(f"remaining notes (all users): {remaining}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
