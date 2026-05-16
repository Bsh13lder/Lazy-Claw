"""One-shot cleanup migrations for the LazyBrain notes table.

The duplicate consolidation pass exists because the v2 lessons migration
updated tag schemas without rewriting note ``title_key``s — so 100+
pre-v2 lesson notes survived under the old ``lesson (topic/outcome):
action: param`` title format, and every replay since ``save_skill_lesson``
shipped its title_key-keyed upsert has piled fresh rows on top instead
of merging into the legacy ones.

This module groups by ``(user_id, title_key)``, keeps the oldest row in
each group, redirects every ``note_links`` edge to the kept id, unions
the duplicate tags into the kept row, and deletes the rest. All inside
a single transaction per group so a partial failure can't strand the
edge table mid-rewrite.

Run via ``lazyclaw cleanup-lazybrain-duplicates`` (see ``cli.py``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lazyclaw.db.connection import db_session
from lazyclaw.lazybrain.store import _dump_tags, _load_tags

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)


async def find_duplicate_groups(
    config: "Config", user_id: str,
) -> list[dict]:
    """Return every duplicate-title group for ``user_id``.

    Each group is ``{title_key, count, ids: [(id, created_at, tags_json)]}``
    sorted with the oldest first inside ``ids`` — that's the row we keep.
    """
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT title_key, COUNT(*) AS n FROM notes "
            "WHERE user_id = ? AND title_key IS NOT NULL "
            "GROUP BY title_key HAVING n > 1 ORDER BY n DESC",
            (user_id,),
        )
        groups_raw = await rows.fetchall()

        groups: list[dict] = []
        for title_key, count in groups_raw:
            id_rows = await db.execute(
                "SELECT id, created_at, tags FROM notes "
                "WHERE user_id = ? AND title_key = ? "
                "ORDER BY created_at ASC, id ASC",
                (user_id, title_key),
            )
            ids = await id_rows.fetchall()
            groups.append({
                "title_key": title_key,
                "count": int(count),
                "ids": [(r[0], r[1], r[2]) for r in ids],
            })
    return groups


async def consolidate_group(
    config: "Config",
    user_id: str,
    group: dict,
) -> dict:
    """Merge every duplicate in ``group`` into the oldest row.

    Returns ``{title_key, kept_id, deleted_ids, redirected_edges}``.
    Runs as a single transaction — if any step fails the whole group
    is rolled back so the edge table never references a deleted row.
    """
    ids = group["ids"]
    if len(ids) < 2:
        return {
            "title_key": group["title_key"],
            "kept_id": ids[0][0] if ids else None,
            "deleted_ids": [],
            "redirected_edges": 0,
        }

    keep_id, _, keep_tags_json = ids[0]
    dupe_rows = ids[1:]
    dupe_ids = [row[0] for row in dupe_rows]

    # Union the tag set across kept + duplicates so any unique
    # outcome/intent variants on the dupes survive.
    merged_tags: list[str] = list(_load_tags(keep_tags_json))
    seen_tags = {t.lower() for t in merged_tags}
    for _, _, dupe_tags_json in dupe_rows:
        for tag in _load_tags(dupe_tags_json):
            if tag.lower() not in seen_tags:
                merged_tags.append(tag)
                seen_tags.add(tag.lower())

    placeholders = ",".join("?" * len(dupe_ids))
    redirected = 0

    async with db_session(config) as db:
        try:
            # Repoint outbound edges (where dupe was the source).
            cur = await db.execute(
                f"UPDATE note_links SET from_note_id = ? "
                f"WHERE user_id = ? AND from_note_id IN ({placeholders})",
                (keep_id, user_id, *dupe_ids),
            )
            redirected += cur.rowcount or 0

            # Repoint inbound edges (where dupe was the target).
            cur = await db.execute(
                f"UPDATE note_links SET to_note_id = ? "
                f"WHERE user_id = ? AND to_note_id IN ({placeholders})",
                (keep_id, user_id, *dupe_ids),
            )
            redirected += cur.rowcount or 0

            # Refresh the kept note's tag column with the unioned set.
            await db.execute(
                "UPDATE notes SET tags = ? WHERE id = ? AND user_id = ?",
                (_dump_tags(merged_tags), keep_id, user_id),
            )

            # Drop the kept id from any self-edges that just appeared
            # because of the redirect (e.g. dupe→keep edges).
            await db.execute(
                "DELETE FROM note_links "
                "WHERE user_id = ? AND from_note_id = to_note_id",
                (user_id,),
            )

            # Cascade should clean up note_links + note_embeddings,
            # but explicit DELETE here keeps behavior deterministic
            # if foreign keys are off on legacy SQLite installs.
            await db.execute(
                f"DELETE FROM note_links WHERE user_id = ? "
                f"AND (from_note_id IN ({placeholders}) "
                f"OR to_note_id IN ({placeholders}))",
                (user_id, *dupe_ids, *dupe_ids),
            )
            await db.execute(
                f"DELETE FROM notes WHERE user_id = ? "
                f"AND id IN ({placeholders})",
                (user_id, *dupe_ids),
            )

            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return {
        "title_key": group["title_key"],
        "kept_id": keep_id,
        "deleted_ids": dupe_ids,
        "redirected_edges": redirected,
    }


async def find_journal_duplicates(
    config: "Config", user_id: str,
) -> list[dict]:
    """Find days that have more than one ``journal/<YYYY-MM-DD>``-tagged
    note. Output mirrors :func:`find_duplicate_groups` so the caller
    can reuse :func:`consolidate_group` to merge them.

    Why a separate pass: title_key dedup misses these because every
    journal stub starts as ``Journal — YYYY-MM-DD`` but the title
    auto-rename pass at journal._maybe_refresh_title rewrites the
    surviving row into a descriptive phrase, leaving each stub with
    a different title_key. The stable handle is the ``journal/<date>``
    tag instead.
    """
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, tags, created_at, content "
            "FROM notes WHERE user_id = ? AND tags LIKE '%\"journal/%'",
            (user_id,),
        )
        rows = await rows.fetchall()

    # Bucket by the journal/<date> tag inside the JSON-encoded tags col.
    import re
    JOURNAL_TAG_RE = re.compile(r'journal/(\d{4}-\d{2}-\d{2})')
    by_date: dict[str, list[tuple[str, str, str, int]]] = {}
    for note_id, tags_json, created_at, content in rows:
        if not tags_json:
            continue
        match = JOURNAL_TAG_RE.search(tags_json)
        if not match:
            continue
        iso_date = match.group(1)
        # Score by content length — the real journal has bullets +
        # wikilinks, the duplicate stubs are ~25 chars of header.
        content_len = len(content or "")
        by_date.setdefault(iso_date, []).append(
            (note_id, created_at, tags_json, content_len)
        )

    groups: list[dict] = []
    for iso_date, members in by_date.items():
        if len(members) < 2:
            continue
        # Keep the longest-content row first so consolidate_group's
        # "ids[0] wins" contract picks the meaningful journal, not a stub.
        # Tiebreaker: oldest created_at — same as title_key dedup.
        members.sort(key=lambda m: (-m[3], m[1] or ""))
        groups.append({
            "title_key": f"<journal/{iso_date}>",  # display only
            "count": len(members),
            "ids": [(nid, ts, tags) for nid, ts, tags, _ in members],
        })
    return groups


async def sweep_orphan_links(
    config: "Config", user_id: str, *, dry_run: bool = False,
) -> dict:
    """Delete ``note_links`` rows that no longer connect to anything.

    Three categories of dangling edge are swept:

    1. **Targets a deleted note** — ``to_note_id`` references a row that
       isn't in ``notes`` anymore. FOREIGN KEY CASCADE handles this on
       modern installs; older DBs with FK off accumulated these.
    2. **Source is a deleted note** — same idea, opposite direction.
    3. **Stale pending wikilinks** — ``to_note_id IS NULL`` and the
       ``to_page_name`` doesn't resolve to any current ``title_key``.
       These are leftover from a [[Foo]] reference whose target was
       renamed or deleted long ago. Kept for 30 days in case the target
       comes back; older ones are dropped so the graph stops showing
       phantom connections.

    Returns ``{user_id, orphans_deleted, stale_pending_deleted}``.
    """
    summary: dict = {
        "user_id": user_id,
        "orphans_deleted": 0,
        "stale_pending_deleted": 0,
        "dry_run": dry_run,
    }
    async with db_session(config) as db:
        # 1+2: edges referencing non-existent notes.
        orphan_q = (
            "SELECT id FROM note_links WHERE user_id = ? AND ("
            "  (to_note_id IS NOT NULL AND to_note_id NOT IN "
            "    (SELECT id FROM notes WHERE user_id = ?))"
            "  OR (from_note_id NOT IN "
            "    (SELECT id FROM notes WHERE user_id = ?))"
            ")"
        )
        rows = await db.execute(orphan_q, (user_id, user_id, user_id))
        orphan_ids = [r[0] for r in await rows.fetchall()]
        summary["orphans_deleted"] = len(orphan_ids)

        # 3: stale pending wikilinks (>30 days, target never resolved).
        stale_q = (
            "SELECT nl.id FROM note_links nl "
            "WHERE nl.user_id = ? AND nl.to_note_id IS NULL "
            "AND nl.to_page_name IS NOT NULL "
            "AND nl.to_page_name NOT IN "
            "  (SELECT title_key FROM notes "
            "   WHERE user_id = ? AND title_key IS NOT NULL) "
            "AND COALESCE(nl.created_at, '') < datetime('now', '-30 days')"
        )
        try:
            rows = await db.execute(stale_q, (user_id, user_id))
            stale_ids = [r[0] for r in await rows.fetchall()]
        except Exception:
            # Older schemas may not have nl.created_at — skip silently.
            logger.debug("stale-pending sweep skipped (older schema?)", exc_info=True)
            stale_ids = []
        summary["stale_pending_deleted"] = len(stale_ids)

        if dry_run:
            return summary

        if orphan_ids:
            placeholders = ",".join("?" * len(orphan_ids))
            await db.execute(
                f"DELETE FROM note_links WHERE id IN ({placeholders})",
                tuple(orphan_ids),
            )
        if stale_ids:
            placeholders = ",".join("?" * len(stale_ids))
            await db.execute(
                f"DELETE FROM note_links WHERE id IN ({placeholders})",
                tuple(stale_ids),
            )
        await db.commit()
    return summary


async def consolidate_user(
    config: "Config",
    user_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Run the full consolidation pass for one user.

    Returns a summary ``{user_id, groups, total_deleted, total_redirected,
    orphans_deleted, stale_pending_deleted}``.
    With ``dry_run=True`` returns the groups it would touch without
    writing anything.

    Three passes:
      1. Title-key dedup — covers the v2-lessons-migration duplicates.
      2. Journal-date dedup — covers the `journal/<date>` stub race in
         journal.py where multiple coroutines each created a fresh
         stub for the same day.
      3. Orphan-link sweep — drops note_links whose source or target
         note no longer exists, plus pending wikilinks older than 30
         days whose target never materialised. Eliminates the "floating
         around with no connections" residue from rename / delete cycles.
    """
    groups = await find_duplicate_groups(config, user_id)
    journal_groups = await find_journal_duplicates(config, user_id)
    groups.extend(journal_groups)
    summary = {
        "user_id": user_id,
        "groups": len(groups),
        "total_deleted": 0,
        "total_redirected": 0,
        "orphans_deleted": 0,
        "stale_pending_deleted": 0,
        "details": [],
        "dry_run": dry_run,
    }

    if dry_run:
        for g in groups:
            summary["details"].append({
                "title_key": g["title_key"],
                "count": g["count"],
                "would_keep": g["ids"][0][0],
                "would_delete": [row[0] for row in g["ids"][1:]],
            })
            summary["total_deleted"] += g["count"] - 1
    else:
        for g in groups:
            try:
                result = await consolidate_group(config, user_id, g)
                summary["details"].append(result)
                summary["total_deleted"] += len(result["deleted_ids"])
                summary["total_redirected"] += result["redirected_edges"]
            except Exception:
                logger.exception(
                    "consolidate_group failed for user=%s title_key=%s",
                    user_id, g["title_key"],
                )
                summary["details"].append({
                    "title_key": g["title_key"],
                    "error": "consolidate failed; rolled back",
                })

    # Always run the orphan-link sweep — it has no overlap with dedup
    # and is a safe-to-rerun idempotent pass.
    try:
        orphan_summary = await sweep_orphan_links(
            config, user_id, dry_run=dry_run,
        )
        summary["orphans_deleted"] = orphan_summary["orphans_deleted"]
        summary["stale_pending_deleted"] = orphan_summary["stale_pending_deleted"]
    except Exception:
        logger.exception("sweep_orphan_links failed for user=%s", user_id)

    return summary


async def consolidate_all_users(
    config: "Config", *, dry_run: bool = False,
) -> list[dict]:
    """Run consolidation against every user. Errors per-user are
    captured into the result dict so one bad user can't abort the run.
    """
    async with db_session(config) as db:
        rows = await db.execute("SELECT id FROM users")
        user_ids = [r[0] for r in await rows.fetchall()]

    out: list[dict] = []
    for uid in user_ids:
        try:
            out.append(await consolidate_user(config, uid, dry_run=dry_run))
        except Exception as exc:
            logger.exception("consolidate_user crashed for %s", uid)
            out.append({"user_id": uid, "error": str(exc)})
    return out
