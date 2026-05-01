"""Encrypted CRUD + backlink indexing for LazyBrain notes.

Data model:
- ``notes(id, user_id, title, content, tags, importance, pinned,
  trace_session_id, title_key, created_at, updated_at)``
- ``note_links(from_note_id, to_page_name, to_note_id, user_id)``

``title`` and ``content`` are AES-256-GCM encrypted with the per-user DEK.
AAD binds ciphertexts to the user and the logical field so a swap attempt
between users or between fields fails authentication.

``title_key`` is the case-folded plaintext of the title — required so
``[[wikilinks]]`` can resolve targets without a decrypt loop over every row.
For a threat model where even titles are sensitive, leave ``title`` empty and
use tag-only pages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import unicodedata
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import (
    decrypt_field,
    encrypt_field,
    user_aad,
)
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.lazybrain.frontmatter import (
    FmValue,
    serialize_frontmatter,
)
from lazyclaw.lazybrain.wikilinks import extract_tags, extract_wikilinks, normalize_page

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Memory recall / context injection — shared exclude tags
# ---------------------------------------------------------------------------
#
# These tag families auto-mirror other stores (personal_memory facts,
# daily/weekly summaries, session caps). Excluding them from
# user-facing memory recall keeps the noise out so genuine notes,
# decisions, ideas surface first. Both `recall_memories` (the skill)
# and `context_builder` (the system-prompt assembler) share this set
# so they never disagree on what counts as "memory".
MEMORY_RECALL_EXCLUDE_TAGS: frozenset[str] = frozenset({
    "memory",        # mirror of personal_memory rows (already in pool)
    "daily-log",     # daily auto-summary
    "session-end",   # session cap
})

# ``kind/*`` shapes are agent-owned replayable artifacts (skill recipes,
# legacy mirrors). They feed the dedicated few-shot exemplar pool via
# `recall_skill_lessons` — they are NOT user-facing facts and should
# not crowd the memory recall.
MEMORY_RECALL_EXCLUDE_KIND_TAGS: frozenset[str] = frozenset({
    "kind/shape",
    "kind/legacy",
})


def is_user_facing_memory_note(note: dict) -> bool:
    """True when a LazyBrain note belongs in user-facing memory recall.

    Drops tag mirrors of other stores AND auto-generated journal /
    summary titles. Used by both the recall skill and context_builder
    so the two never disagree on what's "memory".
    """
    tags = note.get("tags") or []
    if any(t in MEMORY_RECALL_EXCLUDE_TAGS for t in tags):
        return False
    if any(t in MEMORY_RECALL_EXCLUDE_KIND_TAGS for t in tags):
        return False
    title = (note.get("title") or "").strip()
    if title.startswith(("Journal —", "Daily summary", "Weekly summary")):
        return False
    return True


# ---------------------------------------------------------------------------
# AAD builders — bind every ciphertext to (user, field)
# ---------------------------------------------------------------------------

def _title_aad(user_id: str) -> bytes:
    return user_aad(user_id, "notes:title")


def _content_aad(user_id: str) -> bytes:
    return user_aad(user_id, "notes:content")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _dump_tags(tags: list[str] | None) -> str | None:
    if not tags:
        return None
    # Normalise: strip '#', lowercase, dedupe, preserve order
    seen: list[str] = []
    seen_set: set[str] = set()
    for t in tags:
        if not t:
            continue
        norm = t.lstrip("#").strip().lower()
        if norm and norm not in seen_set:
            seen.append(norm)
            seen_set.add(norm)
    return json.dumps(seen) if seen else None


def _load_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return [str(t) for t in parsed if t]
    except (json.JSONDecodeError, TypeError):
        return []


def _title_key(title: str | None) -> str | None:
    if not title:
        return None
    return normalize_page(title)


def _fold(s: str) -> str:
    """Lowercase + strip combining diacritics for substring search.

    Folds Latin accents ("Asistència" → "asistencia", "café" → "cafe")
    so multilingual search isn't case- or accent-sensitive. Non-Latin
    scripts (Georgian, Cyrillic, Arabic, CJK) carry no combining marks
    and pass through unchanged, so this is safe for the user's
    Latin-Georgian transliteration as well.
    """
    if not s:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    ).lower()


def _merge_tags(explicit: list[str] | None, markdown: str) -> list[str]:
    """Tags from the caller plus #hashtags scraped from markdown body."""
    combined: list[str] = []
    seen: set[str] = set()
    for t in explicit or []:
        norm = t.lstrip("#").strip().lower()
        if norm and norm not in seen:
            combined.append(norm)
            seen.add(norm)
    for t in extract_tags(markdown):
        if t not in seen:
            combined.append(t)
            seen.add(t)
    return combined


def _first_line(markdown: str) -> str | None:
    """Derive a title from the first non-empty line if caller didn't supply one."""
    for line in markdown.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:200]
    return None


async def _reindex_links(db, user_id: str, note_id: str, markdown: str) -> list[str]:
    """Replace note_links rows for ``note_id`` with the links extracted from ``markdown``.

    Must run inside an open ``db_session``. Returns the list of wikilink targets.
    """
    targets = extract_wikilinks(markdown)
    await db.execute(
        "DELETE FROM note_links WHERE from_note_id = ?",
        (note_id,),
    )
    if not targets:
        return []

    # Resolve each target against existing titles (case-insensitive)
    placeholders = ",".join("?" * len(targets))
    rows = await db.execute(
        f"SELECT id, title_key FROM notes "
        f"WHERE user_id = ? AND title_key IN ({placeholders})",
        (user_id, *targets),
    )
    resolved = {row[1]: row[0] for row in await rows.fetchall() if row[1]}

    now = _now()
    for target in targets:
        await db.execute(
            "INSERT INTO note_links (user_id, from_note_id, to_page_name, to_note_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, note_id, target, resolved.get(target), now),
        )
    return targets


async def _resolve_pending_links(db, user_id: str, new_note: dict) -> None:
    """If this new note's title fills a previously-unresolved wikilink, backfill it."""
    key = new_note.get("title_key")
    if not key:
        return
    await db.execute(
        "UPDATE note_links SET to_note_id = ? "
        "WHERE user_id = ? AND to_page_name = ? AND to_note_id IS NULL",
        (new_note["id"], user_id, key),
    )


def _schedule_post_save_hooks(
    config: Config, user_id: str, note_id: str, content: str
) -> None:
    """Fire-and-forget secondary indexes after save_note/update_note commits.

    Two hooks the brain needs to keep recall working — both were defined
    elsewhere but never wired:

    1. ``ensure_embedding`` — upserts the note's vector into
       ``note_embeddings`` so ``semantic_search`` actually finds new
       notes. Without this, embeddings.py:semantic_search silently
       degrades to substring matching for everything saved post-startup.
    2. ``wikilink_injector.invalidate_cache`` — drops the 30s LRU of
       known titles so a freshly-titled note can become a ``[[wikilink]]``
       in the agent's next reply, not 30 seconds later.

    Both are best-effort: PKM bookkeeping must never block a save.
    """
    try:
        from lazyclaw.lazybrain import embeddings as _lb_emb
        # Schedule the embedding upsert on the running loop. If we're
        # not inside one (e.g. called from sync test code), silently skip.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            asyncio.create_task(
                _lb_emb.ensure_embedding(config, user_id, note_id, content),
            )
    except Exception:
        logger.debug("schedule embedding upsert failed", exc_info=True)
    try:
        # Local import — wikilink_injector imports store, so this avoids
        # an import-time cycle. invalidate_cache is sync.
        from lazyclaw.runtime.wikilink_injector import invalidate_cache
        invalidate_cache(user_id)
    except Exception:
        logger.debug("wikilink cache invalidation failed", exc_info=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def _existing_journal_title(
    config: Config, user_id: str, date_ymd: str
) -> str | None:
    """Return the exact title of the journal note for date_ymd if one exists,
    else None. Used by save_note to auto-link same-day notes into the day's
    journal page — giving every task/memory/site-memory an automatic graph
    edge to the day it was created.
    """
    expected_key = normalize_page(f"Journal — {date_ymd}")
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT 1 FROM notes WHERE user_id = ? AND title_key = ? LIMIT 1",
            (user_id, expected_key),
        )
        if await row.fetchone():
            return f"Journal — {date_ymd}"
    return None


async def save_note(
    config: Config,
    user_id: str,
    content: str,
    title: str | None = None,
    tags: list[str] | None = None,
    importance: int = 5,
    pinned: bool = False,
    trace_session_id: str | None = None,
    frontmatter: dict[str, FmValue] | None = None,
) -> dict:
    """Create a new note and index its wikilinks. Returns the note dict.

    When ``frontmatter`` is provided, its keys are serialized as a leading
    ``---`` YAML block on top of ``content`` before encryption. The React
    panel parses the same block on read; the body stays editable from
    either side.
    """
    if not content:
        raise ValueError("content required")

    if frontmatter:
        content = serialize_frontmatter(frontmatter, content)

    dek = await get_user_dek(config, user_id)
    note_id = str(uuid4())
    now = _now()
    resolved_title = title if title is not None else _first_line(content)

    # Day-based auto-link: for non-journal notes, append a wikilink to that
    # day's journal page if one exists. Creates the "what happened today"
    # cluster in the graph without any user effort.
    is_journal_note = bool(
        resolved_title
        and (
            resolved_title.startswith("Journal —")
            or resolved_title.startswith("Daily summary")
            or resolved_title.startswith("Weekly summary")
        )
    )
    if not is_journal_note and "[[Journal —" not in content:
        try:
            day = now[:10]  # YYYY-MM-DD
            day_title = await _existing_journal_title(config, user_id, day)
            if day_title:
                content = content.rstrip() + f"\n\n_[[{day_title}]]_"
        except Exception:
            # Never block note creation on an auto-link failure
            pass

    title_key = _title_key(resolved_title)
    enc_title = encrypt_field(resolved_title, dek, _title_aad(user_id))
    enc_content = encrypt_field(content, dek, _content_aad(user_id))
    tags_json = _dump_tags(_merge_tags(tags, content))

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO notes (id, user_id, title, content, tags, importance, "
            "pinned, trace_session_id, title_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                note_id,
                user_id,
                enc_title,
                enc_content,
                tags_json,
                max(1, min(10, int(importance or 5))),
                1 if pinned else 0,
                trace_session_id,
                title_key,
                now,
                now,
            ),
        )
        await _reindex_links(db, user_id, note_id, content)
        await _resolve_pending_links(
            db,
            user_id,
            {"id": note_id, "title_key": title_key},
        )
        await db.commit()

    _schedule_post_save_hooks(config, user_id, note_id, content)

    return {
        "id": note_id,
        "title": resolved_title,
        "content": content,
        "tags": _load_tags(tags_json),
        "importance": max(1, min(10, int(importance or 5))),
        "pinned": bool(pinned),
        "trace_session_id": trace_session_id,
        "title_key": title_key,
        "created_at": now,
        "updated_at": now,
    }


async def get_note(config: Config, user_id: str, note_id: str) -> dict | None:
    """Fetch a single note, decrypted."""
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, title, content, tags, importance, pinned, "
            "trace_session_id, title_key, created_at, updated_at "
            "FROM notes WHERE id = ? AND user_id = ?",
            (note_id, user_id),
        )
        row = await rows.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "title": decrypt_field(row[1], dek, _title_aad(user_id), fallback=""),
        "content": decrypt_field(row[2], dek, _content_aad(user_id), fallback=""),
        "tags": _load_tags(row[3]),
        "importance": row[4],
        "pinned": bool(row[5]),
        "trace_session_id": row[6],
        "title_key": row[7],
        "created_at": row[8],
        "updated_at": row[9],
    }


async def update_note(
    config: Config,
    user_id: str,
    note_id: str,
    *,
    content: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    importance: int | None = None,
    pinned: bool | None = None,
    frontmatter_updates: dict[str, FmValue] | None = None,
) -> dict | None:
    """Partial update. Re-indexes wikilinks if content changed. Returns updated dict.

    ``frontmatter_updates`` merges keys into the note's existing frontmatter
    block (creating one if absent). A value of ``None`` deletes the key —
    useful for clearing transient fields like ``pending_since_turn`` on
    promotion. Pass ``content`` together with ``frontmatter_updates`` only
    when you want to fully replace the body and frontmatter at once;
    typical reinforcement upserts pass only ``frontmatter_updates``.
    """
    existing = await get_note(config, user_id, note_id)
    if not existing:
        return None

    new_content = content if content is not None else existing["content"]
    if frontmatter_updates is not None:
        from lazyclaw.lazybrain.frontmatter import merge_frontmatter
        new_content = merge_frontmatter(new_content, frontmatter_updates)
    new_title = title if title is not None else existing["title"]
    new_tags = tags if tags is not None else existing["tags"]
    new_importance = (
        importance if importance is not None else existing["importance"]
    )
    new_pinned = pinned if pinned is not None else existing["pinned"]

    dek = await get_user_dek(config, user_id)
    enc_title = encrypt_field(new_title, dek, _title_aad(user_id))
    enc_content = encrypt_field(new_content, dek, _content_aad(user_id))
    tags_json = _dump_tags(_merge_tags(new_tags, new_content))
    title_key = _title_key(new_title)
    now = _now()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE notes SET title = ?, content = ?, tags = ?, importance = ?, "
            "pinned = ?, title_key = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (
                enc_title,
                enc_content,
                tags_json,
                max(1, min(10, int(new_importance))),
                1 if new_pinned else 0,
                title_key,
                now,
                note_id,
                user_id,
            ),
        )
        if content is not None:
            await _reindex_links(db, user_id, note_id, new_content)
        # Title changed → either resolve pending backlinks or break stale ones
        if title is not None:
            await db.execute(
                "UPDATE note_links SET to_note_id = NULL "
                "WHERE user_id = ? AND to_note_id = ? AND to_page_name != ?",
                (user_id, note_id, title_key or ""),
            )
            await _resolve_pending_links(
                db,
                user_id,
                {"id": note_id, "title_key": title_key},
            )
        await db.commit()

    # Re-embed if content changed; invalidate wikilink cache if title changed.
    # Frontmatter-only updates also count as a body change because the
    # serialized YAML block is part of the encrypted content blob.
    if content is not None or title is not None or frontmatter_updates is not None:
        _schedule_post_save_hooks(config, user_id, note_id, new_content)

    return await get_note(config, user_id, note_id)


async def delete_note(config: Config, user_id: str, note_id: str) -> bool:
    """Delete a note. ``note_links`` rows clean up via ON DELETE CASCADE."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM notes WHERE id = ? AND user_id = ?",
            (note_id, user_id),
        )
        # Any inbound links now point to a deleted note — null out the pointer
        await db.execute(
            "UPDATE note_links SET to_note_id = NULL "
            "WHERE user_id = ? AND to_note_id = ?",
            (user_id, note_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_notes(
    config: Config,
    user_id: str,
    *,
    tag: str | None = None,
    pinned_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List recent notes, most-recent first."""
    dek = await get_user_dek(config, user_id)

    clauses = ["user_id = ?"]
    params: list = [user_id]
    if pinned_only:
        clauses.append("pinned = 1")
    if tag:
        # Simple substring over JSON array — good enough for typical sizes
        clauses.append("tags LIKE ?")
        params.append(f'%"{tag.lstrip("#").lower()}"%')
    where = " AND ".join(clauses)

    async with db_session(config) as db:
        rows = await db.execute(
            f"SELECT id, title, content, tags, importance, pinned, "
            f"trace_session_id, title_key, created_at, updated_at "
            f"FROM notes WHERE {where} "
            f"ORDER BY pinned DESC, created_at DESC LIMIT ? OFFSET ?",
            (*params, max(1, min(500, limit)), max(0, offset)),
        )
        result = await rows.fetchall()

    out = []
    for row in result:
        out.append(
            {
                "id": row[0],
                "title": decrypt_field(row[1], dek, _title_aad(user_id), fallback=""),
                "content": decrypt_field(row[2], dek, _content_aad(user_id), fallback=""),
                "tags": _load_tags(row[3]),
                "importance": row[4],
                "pinned": bool(row[5]),
                "trace_session_id": row[6],
                "title_key": row[7],
                "created_at": row[8],
                "updated_at": row[9],
            }
        )
    return out


async def search_notes(
    config: Config,
    user_id: str,
    query: str,
    *,
    tag: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Substring search on decrypted content (good enough for thousands of notes).

    Phase 18.5 can swap this for FTS5 without changing the API.
    """
    if not query or not query.strip():
        return []
    q = _fold(query.strip())
    candidates = await list_notes(config, user_id, tag=tag, limit=500)
    hits = [
        n
        for n in candidates
        if q in _fold(n["content"] or "") or q in _fold(n["title"] or "")
    ]
    return hits[:limit]


async def get_backlinks(
    config: Config,
    user_id: str,
    target: str,
) -> list[dict]:
    """Return all notes that link to ``target`` (note_id OR page name)."""
    target_key = normalize_page(target)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT DISTINCT from_note_id FROM note_links "
            "WHERE user_id = ? AND (to_page_name = ? OR to_note_id = ?)",
            (user_id, target_key, target),
        )
        ids = [row[0] for row in await rows.fetchall()]

    notes: list[dict] = []
    for nid in ids:
        n = await get_note(config, user_id, nid)
        if n:
            notes.append(n)
    notes.sort(key=lambda n: n["created_at"], reverse=True)
    return notes


async def find_by_title(
    config: Config, user_id: str, title: str
) -> dict | None:
    """Resolve a wikilink target to a note (most recent match wins)."""
    key = normalize_page(title)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id FROM notes WHERE user_id = ? AND title_key = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, key),
        )
        row = await rows.fetchone()
    if not row:
        return None
    return await get_note(config, user_id, row[0])


async def list_titles(config: Config, user_id: str, limit: int = 2000) -> list[str]:
    """All unique plaintext title keys for this user (cheap, no decrypt)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT DISTINCT title_key FROM notes "
            "WHERE user_id = ? AND title_key IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [row[0] for row in await rows.fetchall() if row[0]]


async def list_tags(config: Config, user_id: str) -> list[dict]:
    """Aggregated tag counts (plaintext field → no decrypt)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT tags FROM notes WHERE user_id = ? AND tags IS NOT NULL",
            (user_id,),
        )
        result = await rows.fetchall()

    counts: dict[str, int] = {}
    for row in result:
        for tag in _load_tags(row[0]):
            counts[tag] = counts.get(tag, 0) + 1
    return [
        {"tag": t, "count": c}
        for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


async def set_pinned(
    config: Config, user_id: str, note_id: str, pinned: bool
) -> bool:
    """Pin / unpin a note. Returns True if the row existed."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "UPDATE notes SET pinned = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (1 if pinned else 0, _now(), note_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
