"""One-time data migrations for LazyBrain notes.

The schema migrations in ``lazyclaw/db/connection.py`` add columns via
``ALTER TABLE`` (cheap, idempotent). The functions in this module fill
*content* into those columns for pre-migration rows — which requires
decrypting the per-user DEK and running classifiers. That's per-user
work, not a global ALTER, so it lives here.

Current migrations
------------------

``backfill_memory_types`` — populates ``notes.memory_type`` for rows that
predate the 2026-05-20 typed-taxonomy column. Idempotent: each call only
touches rows where ``memory_type IS NULL``. Fires once on startup via
``backfill_memory_types_all_users``; subsequent calls are no-ops once
every user is classified.

Why this matters
----------------

Until existing rows are classified, ``is_auto_inject_type(None)`` fails
closed and excludes them from the system prompt entirely. That's safe
(they don't contaminate) but it's not useful — the brain can't pull
typed knowledge that's still NULL. The backfill turns the lights back
on by labelling them in bulk.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from lazyclaw.crypto.encryption import decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.lazybrain.memory_types import infer_memory_type
from lazyclaw.lazybrain.store import (
    _content_aad,
    _load_tags,
    _title_aad,
)

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)


# Per-batch SELECT/UPDATE cap. Loop in :func:`backfill_memory_types`
# keeps draining until no NULL rows remain, but each individual
# transaction stays bounded so we don't pin the event loop or hold one
# huge write lock — the same constraint that motivated the original
# 500-row cap (see commit 2978591: backfill must not block startup).
_BACKFILL_BATCH_LIMIT: int = 500

# Hard safety ceiling on the drain loop. At 500 rows/batch this lets a
# single user get up to 50,000 notes typed per startup — generous for
# the realistic ceiling — while preventing an infinite loop if a future
# bug ever caused a batch to scan rows without updating them.
_BACKFILL_MAX_ITERATIONS: int = 100

# Cooperative yield between batches. Lets other coroutines (and the
# DB connection pool) breathe; tests run on an in-memory DB where this
# is a no-op cost.
_BACKFILL_BATCH_SLEEP_SECONDS: float = 0.05


async def backfill_memory_types(
    config: "Config", user_id: str,
) -> dict:
    """Classify and persist ``memory_type`` for the user's NULL-typed notes.

    Returns a summary dict::

        {"user_id": str, "scanned": int, "updated": int, "errors": int,
         "skipped": int}

    ``scanned`` is the number of rows the SELECT returned (capped at
    :data:`_BACKFILL_BATCH_LIMIT`). ``updated`` is rows whose
    ``memory_type`` we wrote. ``skipped`` is rows whose decrypt failed
    (cipher mismatch / wrong DEK / corruption) — they remain NULL and
    are picked up on the next call once the underlying issue resolves.

    Idempotent: each call narrows to ``memory_type IS NULL``, so once a
    row is classified it never re-classifies. Safe to call on every
    startup.
    """
    try:
        dek = await get_user_dek(config, user_id)
    except Exception as exc:
        logger.warning(
            "backfill_memory_types: skipping user=%s — get_user_dek failed: %s",
            user_id, exc,
        )
        return {
            "user_id": user_id, "scanned": 0, "updated": 0,
            "errors": 1, "skipped": 0,
        }

    title_aad = _title_aad(user_id)
    content_aad = _content_aad(user_id)

    scanned_total = 0
    updated_total = 0
    skipped_total = 0
    errors_total = 0

    # Drain loop: keep pulling NULL-typed batches until the SELECT
    # returns zero rows. Each iteration is bounded by
    # ``_BACKFILL_BATCH_LIMIT`` so the event loop stays responsive and
    # the write transaction stays small. ``_BACKFILL_MAX_ITERATIONS``
    # is a defensive cap — if a batch ever scans rows without producing
    # at least one ``updated`` (i.e. everything either skipped or
    # errored), forward progress requires breaking out so we don't spin
    # on the same NULL rows forever.
    for iteration in range(1, _BACKFILL_MAX_ITERATIONS + 1):
        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT id, title, content, tags "
                "FROM notes "
                "WHERE user_id = ? AND memory_type IS NULL "
                "ORDER BY created_at ASC "
                "LIMIT ?",
                (user_id, _BACKFILL_BATCH_LIMIT),
            )
            candidates = await rows.fetchall()

        if not candidates:
            break

        scanned = len(candidates)
        batch_updated = 0
        batch_skipped = 0
        batch_errors = 0

        # Classify first, then write all UPDATEs in one transaction.
        # Per-row ``async with db_session`` + commit was a perf hot-spot
        # on the startup pass — for a user with 500 pre-migration notes
        # that's 500 commits, blocking the lifespan handler and
        # returning 502 from nginx until done. One batched transaction
        # is ~100× faster.
        classified: list[tuple[str, str]] = []  # (note_id, mtype)
        for row in candidates:
            note_id, enc_title, enc_content, tags_json = row
            try:
                title = decrypt_field(
                    enc_title, dek, title_aad, fallback="",
                )
                content = decrypt_field(
                    enc_content, dek, content_aad, fallback="",
                )
            except Exception:
                # Encrypted rows that fail to decrypt (cipher schema
                # drift, wrong DEK, manually edited DB) leave the row
                # NULL and move on — never raise out of a startup
                # migration.
                logger.debug(
                    "backfill_memory_types: decrypt failed "
                    "for note=%s user=%s",
                    note_id, user_id,
                )
                batch_skipped += 1
                continue

            if not (title or content):
                batch_skipped += 1
                continue

            tags = _load_tags(tags_json) if tags_json else []
            try:
                mtype = infer_memory_type(content, tags=tags, title=title)
            except Exception:
                logger.debug(
                    "backfill_memory_types: classifier crashed on note=%s",
                    note_id, exc_info=True,
                )
                batch_errors += 1
                continue

            classified.append((note_id, mtype))

        if classified:
            try:
                async with db_session(config) as db:
                    for note_id, mtype in classified:
                        await db.execute(
                            "UPDATE notes SET memory_type = ? "
                            "WHERE id = ? AND user_id = ? "
                            "AND memory_type IS NULL",
                            (mtype, note_id, user_id),
                        )
                    await db.commit()
                batch_updated = len(classified)
            except Exception:
                logger.exception(
                    "backfill_memory_types: batched UPDATE failed "
                    "user=%s (%d rows lost this pass — "
                    "next startup retries)",
                    user_id, len(classified),
                )
                batch_errors += len(classified)

        scanned_total += scanned
        updated_total += batch_updated
        skipped_total += batch_skipped
        errors_total += batch_errors

        logger.info(
            "memory_type backfill: typed %d rows (batch %d)",
            batch_updated, iteration,
        )

        # No forward progress on the NULL set this iteration → break
        # rather than spin. Skipped/errored rows stay NULL by design
        # (they'll be retried on the next startup once the underlying
        # issue resolves) and continuing the loop would just re-scan
        # the same rows.
        if batch_updated == 0:
            break

        # Cooperative yield. Lets other coroutines and the DB pool
        # breathe before the next batch.
        await asyncio.sleep(_BACKFILL_BATCH_SLEEP_SECONDS)
    else:
        # ``for ... else`` fires only when the loop exhausts the range
        # — i.e. we hit the safety ceiling without draining the set.
        logger.warning(
            "backfill_memory_types user=%s hit safety ceiling "
            "(%d iterations × %d rows) — re-run on next startup",
            user_id, _BACKFILL_MAX_ITERATIONS, _BACKFILL_BATCH_LIMIT,
        )

    if scanned_total:
        logger.info(
            "backfill_memory_types user=%s scanned=%d updated=%d "
            "skipped=%d errors=%d",
            user_id, scanned_total, updated_total,
            skipped_total, errors_total,
        )

    # Verify the drain is actually complete before declaring victory.
    # If skipped/errored rows still hold NULL, surface the residual
    # count so ops can spot a stuck migration in the log.
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT COUNT(*) FROM notes "
            "WHERE user_id = ? AND memory_type IS NULL",
            (user_id,),
        )
        residual_row = await rows.fetchone()
    residual = int(residual_row[0]) if residual_row else 0
    if residual == 0 and updated_total:
        logger.info(
            "memory_type backfill complete: 0 NULL rows remaining",
        )
    elif residual:
        logger.info(
            "memory_type backfill: %d NULL rows remain for user=%s "
            "(skipped/errored — retried next startup)",
            residual, user_id,
        )

    return {
        "user_id": user_id,
        "scanned": scanned_total,
        "updated": updated_total,
        "errors": errors_total,
        "skipped": skipped_total,
    }


async def backfill_memory_types_all_users(config: "Config") -> list[dict]:
    """Run :func:`backfill_memory_types` for every user.

    Per-user errors are captured into the result list so one corrupt
    user can't abort the whole startup pass. Mirrors the pattern used
    by ``lazyclaw.lazybrain.cleanup.consolidate_all_users``.
    """
    async with db_session(config) as db:
        rows = await db.execute("SELECT id FROM users")
        user_ids = [r[0] for r in await rows.fetchall()]

    out: list[dict] = []
    for uid in user_ids:
        try:
            out.append(await backfill_memory_types(config, uid))
        except Exception as exc:
            logger.exception(
                "backfill_memory_types crashed for user=%s", uid,
            )
            out.append({"user_id": uid, "error": str(exc)})

    # Compact one-line summary at the end so the startup log surfaces
    # total work without dumping every per-user line.
    total_updated = sum(r.get("updated", 0) for r in out if "updated" in r)
    total_scanned = sum(r.get("scanned", 0) for r in out if "scanned" in r)
    if total_scanned:
        logger.info(
            "backfill_memory_types_all_users users=%d scanned=%d updated=%d",
            len(user_ids), total_scanned, total_updated,
        )
    return out
