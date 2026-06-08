"""Lesson storage — routes lessons to the right memory system.

Site-specific lessons → site_memory (per-domain, encrypted, success/fail tracking).
User preferences → personal_memory (global per-user, encrypted).

Both systems already inject their memories into agent context automatically:
- personal_memory via context_builder.py
- site_memory via browser_skill.py (injected into browser tool results)
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.runtime.lesson_extractor import Lesson

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Content-addressable dedup key
# ---------------------------------------------------------------------------
#
# The OLD scheme keyed the LazyBrain mirror note on ``Lesson: {content[:60]}``
# and looked it up via ``find_by_title``. That is brittle: any drift past
# char 60 (or a NULL/empty title) produced a brand-new row every save, so a
# repeated correction such as
#   ``learned_preference: for [job:upwork_inbox_watch], call upwork_get_mess…``
# stacked up 6-7 duplicates in the graph.
#
# The fix mirrors ``skill_lesson.py``'s content-addressable upsert: a stable
# title derived from a hash of the *full normalized content* (plus a short
# human-readable prefix for the UI). Identical content → identical title →
# identical ``title_key`` → upsert into the same note instead of inserting.


_WS_RE = re.compile(r"\s+")
_CONTENT_HASH_LEN = 12      # hex chars of the digest kept in the title
_TITLE_PREFIX_LEN = 48      # readable slice shown before the hash


def _normalize_for_key(content: str) -> str:
    """Collapse whitespace + lowercase so trivial formatting drift on the
    same fact still maps to one key. Mirrors the intent of
    ``skill_lesson._intent_slug`` (stable handle, not the raw bytes)."""
    return _WS_RE.sub(" ", (content or "").strip()).lower()


def _content_hash(content: str) -> str:
    """Short hex digest of the normalized content. Deterministic across
    runs/processes — the property the brittle ``[:60]`` slice lacked."""
    norm = _normalize_for_key(content)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:_CONTENT_HASH_LEN]


def lesson_canonical_title(content: str) -> str:
    """One stable title per distinct lesson content — the upsert handle.

    ``Lesson: <readable slice> [<hash>]``. The hash is the load-bearing
    part (guarantees same-content → same title_key); the readable slice is
    purely cosmetic for the PKM list view. Re-saving the same correction
    replays into the same card rather than stacking a duplicate.
    """
    norm = _normalize_for_key(content)
    readable = norm[:_TITLE_PREFIX_LEN].rstrip()
    return f"Lesson: {readable} [{_content_hash(content)}]"


async def _find_note_by_title_key(
    config: "Config", user_id: str, title_key: str | None
) -> dict | None:
    """Return the existing note for ``title_key`` (decrypted) or None.

    Mirrors ``skill_lesson._find_by_title_key`` — a direct SELECT on the
    indexed plaintext ``title_key`` column, then the public ``get_note`` so
    decryption + tag-loading stay consistent. Never raises (fire-and-forget
    write path — a lookup failure must degrade to "no existing note" so the
    caller falls through to a normal save).
    """
    if not title_key:
        return None
    try:
        from lazyclaw.db.connection import db_session
        from lazyclaw.lazybrain import store as lb_store

        async with db_session(config) as db:
            rows = await db.execute(
                "SELECT id FROM notes WHERE user_id = ? AND title_key = ? LIMIT 1",
                (user_id, title_key),
            )
            row = await rows.fetchone()
        if not row:
            return None
        return await lb_store.get_note(config, user_id, row[0])
    except Exception:
        logger.debug("lesson_store: find_by_title_key failed", exc_info=True)
        return None


async def store_lesson(
    config: Config,
    user_id: str,
    lesson: Lesson,
    url: str | None = None,
) -> str | None:
    """Store a lesson in the appropriate memory system.

    Returns memory ID on success, None on failure. Never raises.
    """
    try:
        if lesson.lesson_type == "site" and lesson.domain and url:
            from lazyclaw.browser.site_memory import remember

            memory_id = await remember(
                config,
                user_id,
                url,
                memory_type="custom",
                title=f"Learned: {lesson.content[:50]}",
                content={"lesson": lesson.content, "source": "correction"},
            )
            logger.info(
                "Stored site lesson for %s: %s (id=%s)",
                lesson.domain, lesson.content[:60], memory_id,
            )
            return memory_id
        else:
            from lazyclaw.memory.personal import save_memory

            memory_id = await save_memory(
                config,
                user_id,
                content=lesson.content,
                memory_type="learned_preference",
                importance=lesson.importance,
            )
            logger.info(
                "Stored preference lesson: %s (id=%s, importance=%d)",
                lesson.content[:60], memory_id, lesson.importance,
            )
            # Also mirror into LazyBrain so the user can browse + backlink
            # the lesson in the PKM UI. Fire-and-forget; matches the
            # defensive pattern used elsewhere in this module.
            #
            # Dedup: a repeated user correction with the same first 60 chars
            # of content used to create a fresh note row every time, and the
            # ``content[:60]`` title was brittle — any drift past char 60 (or
            # a NULL title) sidestepped the find-by-title check and stacked a
            # duplicate. Now the title is content-addressable (hash of the
            # FULL normalized content), so the same correction upserts into
            # the same card. Pattern mirrors skill_lesson.py's title_key
            # upsert. Lookup goes through the indexed ``title_key`` column.
            try:
                from lazyclaw.lazybrain import store as lb_store
                from lazyclaw.lazybrain import events as lb_events
                from lazyclaw.lazybrain.store import _title_key

                tags = ["lesson", "auto", "owner/agent"]
                if lesson.lesson_type == "site" and lesson.domain:
                    tags.append(f"site/{lesson.domain}")
                canonical_title = lesson_canonical_title(lesson.content)
                existing = await _find_note_by_title_key(
                    config, user_id, _title_key(canonical_title),
                )
                if existing is not None:
                    existing_tags = existing.get("tags") or []
                    merged_tags = list({*existing_tags, *tags})
                    note = await lb_store.update_note(
                        config, user_id, existing["id"],
                        content=lesson.content,
                        tags=merged_tags,
                        importance=lesson.importance,
                    ) or existing
                else:
                    note = await lb_store.save_note(
                        config,
                        user_id,
                        content=lesson.content,
                        title=canonical_title,
                        tags=tags,
                        importance=lesson.importance,
                    )
                lb_events.publish_note_saved(
                    user_id, note["id"], note["title"], note["tags"], source="lesson",
                )
            except Exception:
                logger.warning(
                    "lazybrain lesson mirror failed for user %s",
                    user_id, exc_info=True,
                )
            return memory_id

    except Exception as e:
        logger.warning("Failed to store lesson: %s", e)
        return None
