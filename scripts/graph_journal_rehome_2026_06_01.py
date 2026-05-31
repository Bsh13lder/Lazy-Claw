"""Re-home cross-user journal pages + rebind dangling journal links (2026-06-01).

Background (cross-user journal bug):
  * ``heartbeat/daemon._seed_today_journals`` used to seed a journal for EVERY
    row in ``users`` -- including a dead account and ``u-test`` that own nothing
    but journal stubs. Result: duplicate journal pages per day across users.
  * The real user's ``[[Journal -- DATE]]`` backlinks therefore resolved against
    journals owned by the dead user (or got NULLed on title refresh), leaving
    ~100 dangling journal links.

This script repairs the existing DB. It is DRY-RUN by default -- set ``APPLY=1``
to mutate. It takes an online backup (with integrity + count verification)
before any write, and ABORTS if the backup fails. It NEVER deletes the real
user's data; only dead-user duplicates are removed (via the app's own
``store.delete_note`` so dependent rows are swept).

Note: ``db_session`` does not set a ``row_factory``, so cursors return plain
tuples. We access every column positionally and keep the SELECT column order
explicit, right next to the unpacking, to stay legible.

Run INSIDE the lazyclaw container so SQLite WAL locking stays coherent:
    docker exec [-e APPLY=1] -e TS=<utc-stamp> lazyclaw \
        python3 /app/scripts/graph_journal_rehome_2026_06_01.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, get_db_path
from lazyclaw.lazybrain import store

# The single active/real user. Everyone else is treated as dead/test for the
# purpose of journal ownership. We ALSO derive activity from the schema (a user
# is "active" iff they own a non-journal note) so this constant is a guard, not
# the sole signal -- the real user must pass BOTH checks before we trust a
# delete.
REAL_USER = "a7ac3e09-62be-4b1a-8411-b6a97549fe27"

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_JOURNAL_PAGE_RE = re.compile(r"^\s*journal\s*[—-]\s*\d{4}-\d{2}-\d{2}\s*$", re.I)


def _tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(t) for t in v] if isinstance(v, list) else []
    except Exception:
        return []


def _is_journal_only(tags: list[str]) -> bool:
    return any(t.startswith("journal/") for t in tags)


def _journal_day(tags: list[str]) -> str | None:
    for t in tags:
        if t.startswith("journal/"):
            return t.split("/", 1)[1]
    return None


# Note rows are ``(id, user_id, tags, title_key, aliases)`` tuples.
_N_ID, _N_USER, _N_TAGS, _N_TITLE_KEY, _N_ALIASES = range(5)
# Dangling link rows are ``(rowid, user_id, from_note_id, to_note_id, page)``.
_L_ROWID, _L_USER, _L_FROM, _L_TO, _L_PAGE = range(5)


def _active_user_ids(note_rows: list[tuple]) -> set[str]:
    """Users owning at least one NON-journal note (matches the daemon predicate)."""
    active: set[str] = set()
    for r in note_rows:
        if not _is_journal_only(_tags(r[_N_TAGS])):
            active.add(r[_N_USER])
    return active


def _journals_by_day(note_rows: list[tuple], user_id: str) -> dict[str, str]:
    """day -> note_id for the given user's journal pages (last write wins)."""
    out: dict[str, str] = {}
    for r in note_rows:
        if r[_N_USER] != user_id:
            continue
        day = _journal_day(_tags(r[_N_TAGS]))
        if day:
            out[day] = r[_N_ID]
    return out


async def main() -> None:  # noqa: C901 - reporting script
    apply = os.environ.get("APPLY") == "1"
    cfg = Config()
    db_path = str(get_db_path(cfg))

    # -- Gather (read-only) -------------------------------------------------
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT id, user_id, tags, title_key, aliases FROM notes"
        )
        note_rows = list(await cur.fetchall())
        cur = await db.execute(
            "SELECT rowid, user_id, from_note_id, to_note_id, to_page_name "
            "FROM note_links WHERE to_note_id IS NULL"
        )
        dangling = list(await cur.fetchall())

    active = _active_user_ids(note_rows)
    real_active = REAL_USER in active
    print(f"db: {db_path}")
    print(f"active users (own a non-journal note): {sorted(active)}")
    print(f"REAL_USER active+present: {real_active}")
    if not real_active:
        print("REAL_USER is not active in this DB -- ABORTING (refusing to guess).")
        await close_pool()
        return

    dead_users = {r[_N_USER] for r in note_rows} - active
    print(f"dead/test users: {sorted(dead_users)}")

    real_journals = _journals_by_day(note_rows, REAL_USER)
    dead_journals: dict[str, dict[str, str]] = {
        u: _journals_by_day(note_rows, u) for u in dead_users
    }
    print(f"real journal days: {len(real_journals)}")
    for u, dj in dead_journals.items():
        print(f"  dead {u[:8]} journal days: {len(dj)}")

    # -- Plan ---------------------------------------------------------------
    # For each of the REAL user's dangling journal-named links, decide how it
    # would bind. We rebind to the real user's OWN journal for that day when one
    # exists; otherwise we re-home a dead user's journal for that day to the
    # real user and bind to it.
    rehome_note_ids: dict[str, str] = {}  # dead note_id -> day (to re-home)
    bind_plan: list[tuple[int, str]] = []  # (link rowid, target_note_id)
    no_journal_days: set[str] = set()
    real_journal_links = [
        d for d in dangling
        if d[_L_USER] == REAL_USER and _JOURNAL_PAGE_RE.match((d[_L_PAGE] or ""))
    ]

    for link in real_journal_links:
        m = _DATE_RE.search(link[_L_PAGE] or "")
        if not m:
            continue
        day = m.group(1)
        target = real_journals.get(day)
        if target is None:
            # Look for a dead user's journal we can re-home for that day.
            for u, dj in dead_journals.items():
                if day in dj:
                    target = dj[day]
                    rehome_note_ids[target] = day
                    break
        if target is None:
            no_journal_days.add(day)
            continue
        bind_plan.append((link[_L_ROWID], target))

    # Duplicate dead journals on days the real user ALSO owns -> deletable.
    deletable_dupes: dict[str, str] = {}  # dead note_id -> owner_user_id
    owner_of = {r[_N_ID]: r[_N_USER] for r in note_rows}
    for u, dj in dead_journals.items():
        for day, nid in dj.items():
            if day in real_journals and nid not in rehome_note_ids:
                deletable_dupes[nid] = u

    print("\n-- PLAN -------------------------------------------------")
    print(f"real dangling journal-named links: {len(real_journal_links)}")
    print(f"links that WOULD resolve: {len(bind_plan)}")
    print(f"journals WOULD re-home (dead -> real): {len(rehome_note_ids)}")
    print(f"dead duplicate journals WOULD delete: {len(deletable_dupes)}")
    print(f"days with NO journal anywhere (left dangling): {len(no_journal_days)}")
    if no_journal_days:
        print(f"   {sorted(no_journal_days)}")

    if not apply:
        print("\nDRY-RUN (set APPLY=1 to execute). Nothing changed.")
        await close_pool()
        return

    # -- BACKUP FIRST (abort on any failure) --------------------------------
    ts = os.environ.get("TS") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = f"{os.path.dirname(db_path)}/backups"
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = f"{backup_dir}/lazyclaw.pre-journal-rehome.{ts}.db"
    print(f"\nBacking up -> {backup_path}")
    total_notes = len(note_rows)
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
    if ic != "ok" or bcount != total_notes:
        print("BACKUP VERIFICATION FAILED -- ABORTING, nothing changed.")
        await close_pool()
        return

    # -- APPLY --------------------------------------------------------------
    # 1) Re-home dead journals to the real user (UPDATE user_id + link owner).
    rehomed = 0
    async with db_session(cfg) as db:
        for nid, _day in rehome_note_ids.items():
            await db.execute(
                "UPDATE notes SET user_id = ? WHERE id = ?", (REAL_USER, nid)
            )
            await db.execute(
                "UPDATE note_links SET user_id = ? "
                "WHERE to_note_id = ? OR from_note_id = ?",
                (REAL_USER, nid, nid),
            )
            rehomed += 1
        # 2) Bind the planned dangling links to their targets.
        bound = 0
        for rowid, target in bind_plan:
            await db.execute(
                "UPDATE note_links SET to_note_id = ? "
                "WHERE rowid = ? AND to_note_id IS NULL",
                (target, rowid),
            )
            bound += 1
        await db.commit()

    # 3) Delete dead duplicate journals via the app (sweeps dependents). Guard:
    #    never delete a note the real user owns.
    deleted = 0
    for nid, dead_owner in deletable_dupes.items():
        if owner_of.get(nid) == REAL_USER:
            continue  # safety -- should never happen
        if await store.delete_note(cfg, dead_owner, nid):
            deleted += 1

    # 4) Final resolution pass -- bind any remaining dangling links whose page
    #    name now matches a real-user note's title_key OR alias.
    resolved_extra = 0
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT id, title_key, aliases FROM notes WHERE user_id = ?",
            (REAL_USER,),
        )
        key_to_id: dict[str, str] = {}
        for nid, title_key, aliases in await cur.fetchall():
            tk = (title_key or "").strip().lower()
            if tk:
                key_to_id.setdefault(tk, nid)
            for a in _tags(aliases):  # aliases share the JSON-list shape
                ak = a.strip().lower()
                if ak:
                    key_to_id.setdefault(ak, nid)
        cur = await db.execute(
            "SELECT rowid, to_page_name FROM note_links "
            "WHERE user_id = ? AND to_note_id IS NULL",
            (REAL_USER,),
        )
        for rowid, page in await cur.fetchall():
            tid = key_to_id.get((page or "").strip().lower())
            if tid:
                await db.execute(
                    "UPDATE note_links SET to_note_id = ? WHERE rowid = ?",
                    (tid, rowid),
                )
                resolved_extra += 1
        await db.commit()

    print("\n-- APPLIED ----------------------------------------------")
    print(f"journals re-homed (dead -> real): {rehomed}")
    print(f"dangling links bound (planned): {bound}")
    print(f"dead duplicate journals deleted: {deleted}")
    print(f"extra links resolved by alias/title pass: {resolved_extra}")

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM note_links "
            "WHERE user_id = ? AND to_note_id IS NULL",
            (REAL_USER,),
        )
        remaining = (await cur.fetchone())[0]
    print(f"real-user dangling links remaining: {remaining}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
