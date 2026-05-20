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


# Cap per-call to avoid pinning the event loop on a fresh install with
# thousands of notes. Subsequent calls keep chipping away — idempotent
# on the WHERE filter so partial runs are safe.
_BACKFILL_BATCH_LIMIT: int = 500


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

    scanned = len(candidates)
    updated = 0
    skipped = 0
    errors = 0

    # Classify first, then write all UPDATEs in one transaction. Per-row
    # ``async with db_session`` + commit was a perf hot-spot on the
    # startup pass — for a user with 500 pre-migration notes that's
    # 500 commits, blocking the lifespan handler and returning 502 from
    # nginx until done. One batched transaction is ~100× faster.
    classified: list[tuple[str, str]] = []  # (note_id, mtype)
    for row in candidates:
        note_id, enc_title, enc_content, tags_json = row
        try:
            title = decrypt_field(enc_title, dek, title_aad, fallback="")
            content = decrypt_field(enc_content, dek, content_aad, fallback="")
        except Exception:
            # Encrypted rows that fail to decrypt (cipher schema drift,
            # wrong DEK, manually edited DB) leave the row NULL and
            # move on — never raise out of a startup migration.
            logger.debug(
                "backfill_memory_types: decrypt failed for note=%s user=%s",
                note_id, user_id,
            )
            skipped += 1
            continue

        if not (title or content):
            skipped += 1
            continue

        tags = _load_tags(tags_json) if tags_json else []
        try:
            mtype = infer_memory_type(content, tags=tags, title=title)
        except Exception:
            logger.debug(
                "backfill_memory_types: classifier crashed on note=%s",
                note_id, exc_info=True,
            )
            errors += 1
            continue

        classified.append((note_id, mtype))

    if classified:
        try:
            async with db_session(config) as db:
                for note_id, mtype in classified:
                    await db.execute(
                        "UPDATE notes SET memory_type = ? "
                        "WHERE id = ? AND user_id = ? AND memory_type IS NULL",
                        (mtype, note_id, user_id),
                    )
                await db.commit()
            updated = len(classified)
        except Exception:
            logger.exception(
                "backfill_memory_types: batched UPDATE failed user=%s "
                "(%d rows lost this pass — next startup retries)",
                user_id, len(classified),
            )
            errors += len(classified)

    if scanned:
        logger.info(
            "backfill_memory_types user=%s scanned=%d updated=%d "
            "skipped=%d errors=%d",
            user_id, scanned, updated, skipped, errors,
        )

    return {
        "user_id": user_id,
        "scanned": scanned,
        "updated": updated,
        "errors": errors,
        "skipped": skipped,
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
