"""Typed-memory recall — the on-demand fetch path for the brain.

Phase 1 added a ``notes.memory_type`` column + deterministic classifier.
Phase 2 (sibling agent) narrows auto-injection into the cached system
prompt to ``AUTO_INJECT_TYPES`` only (``user`` | ``feedback`` |
``project`` | ``reference``). Phase 3 — this module — gives the brain
the recovery path: it can ASK for any memory_type explicitly and get
back ONLY canonical typed memories, never paraphrased session-log dumps.

Why this lives in its own module rather than inside ``store.py``:

  - ``store.py`` is the encrypted CRUD surface. Composing semantic
    search + wikilink graph crawl + memory_type backfill belongs at a
    higher abstraction layer, not in the column-level CRUD path.
  - Decouples Phase 3 plumbing from Phase 1 (Phase 1 owns the schema +
    classifier; this module owns retrieval composition).

The skill at
:mod:`lazyclaw.skills.builtin.lazybrain.recall_typed_memory` wraps
:func:`recall_typed_memory` as an NL tool the agent can dispatch.

References:
  - MEMORY/feedback-claude-cache-amplifies-stale-context — why this
    on-demand fetch path exists.
  - MEMORY/project-f1-mechanical-enforcement — why the brain must
    quote-then-summarize, which means fetching typed memories before
    asserting anything.
  - commit 3de968c — Phase 1 (memory_type column + classifier).
"""
from __future__ import annotations

import logging
from typing import Iterable

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.lazybrain import store
from lazyclaw.lazybrain.memory_types import (
    DEFAULT_MEMORY_TYPE,
    MEMORY_TYPES,
    normalize_memory_type,
)
from lazyclaw.lazybrain.wikilinks import normalize_page

logger = logging.getLogger(__name__)


async def _memory_type_of(
    config: Config, user_id: str, note_id: str,
) -> str | None:
    """Cheap one-column fetch.

    :func:`store.get_note` predates the column and intentionally stays
    on its current SELECT list to avoid disturbing other callers.
    Returning ``None`` on a row mismatch is fine; the caller treats it
    as "unknown" which fails the type filter.
    """
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT memory_type FROM notes WHERE id = ? AND user_id = ?",
            (note_id, user_id),
        )
        row = await rows.fetchone()
    return row[0] if row else None


async def _wikilink_neighbours(
    config: Config,
    user_id: str,
    target_key: str,
    target_note_id: str | None,
) -> list[str]:
    """Return ``from_note_id`` rows whose wikilink edges point AT
    ``target_key``.

    Uses ``note_links.edge_type = 'wikilink'`` so we don't pull semantic
    edges (``supersedes`` / ``contradicts`` / ``references``). Matches
    either via ``to_page_name`` (literal key) or via ``to_note_id`` when
    the target has an actual note backing the title.
    """
    async with db_session(config) as db:
        if target_note_id:
            rows = await db.execute(
                "SELECT DISTINCT from_note_id FROM note_links "
                "WHERE user_id = ? AND edge_type = 'wikilink' "
                "AND (to_page_name = ? OR to_note_id = ?)",
                (user_id, target_key, target_note_id),
            )
        else:
            rows = await db.execute(
                "SELECT DISTINCT from_note_id FROM note_links "
                "WHERE user_id = ? AND edge_type = 'wikilink' "
                "AND to_page_name = ?",
                (user_id, target_key),
            )
        return [r[0] for r in await rows.fetchall() if r[0]]


def _normalize_type_filter(
    memory_type: (
        str
        | set[str]
        | frozenset[str]
        | list[str]
        | tuple[str, ...]
        | None
    ),
) -> set[str] | None:
    """Coerce the ``memory_type`` kwarg to a normalised set or ``None``.

    ``None`` means "all types". An empty iterable also normalises to
    ``None`` so a caller passing ``memory_type=[]`` doesn't get an
    empty-set filter that drops every row.
    """
    if memory_type is None:
        return None
    if isinstance(memory_type, str):
        raw_iter: Iterable[str] = (memory_type,)
    else:
        raw_iter = tuple(memory_type)
    types_filter = {normalize_memory_type(t) for t in raw_iter if t}
    return types_filter or None


def _matches_type(
    note_type: str | None, types_filter: set[str] | None,
) -> bool:
    if types_filter is None:
        return True
    return (note_type or "") in types_filter


async def query_by_memory_type(
    config: Config,
    user_id: str,
    *,
    memory_type: (
        str
        | set[str]
        | frozenset[str]
        | list[str]
        | tuple[str, ...]
        | None
    ) = None,
    query: str | None = None,
    wikilink_target: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Fetch notes filtered by ``memory_type``, optionally narrowed by a
    semantic ``query`` or by inbound wikilinks to ``wikilink_target``.

    At least one of ``memory_type`` / ``query`` / ``wikilink_target``
    MUST be non-empty — calling with all three None is almost always a
    bug (asks for "every note for this user"), so we raise.

    Returns a list of note dicts (same shape as :func:`store.get_note`
    plus an optional ``_score`` field when ``query`` was used, and an
    explicit ``memory_type`` key on every row).
    """
    if (
        memory_type is None
        and not (query and query.strip())
        and not (wikilink_target and wikilink_target.strip())
    ):
        raise ValueError(
            "query_by_memory_type requires at least one of "
            "memory_type / query / wikilink_target"
        )

    types_filter = _normalize_type_filter(memory_type)
    capped = max(1, min(200, int(limit)))

    wikilink_ids: list[str] = []
    if wikilink_target and wikilink_target.strip():
        target_key = normalize_page(wikilink_target)
        target_note = await store.find_by_title(
            config, user_id, wikilink_target,
        )
        wikilink_ids = await _wikilink_neighbours(
            config, user_id, target_key,
            target_note["id"] if target_note else None,
        )

    semantic_hits: list[dict] = []
    if query and query.strip():
        try:
            from lazyclaw.lazybrain import embeddings as _emb
            data = await _emb.semantic_search(
                config, user_id, query, k=max(capped * 3, 30),
            )
            semantic_hits = list(data.get("results") or [])
        except Exception:
            logger.debug(
                "semantic_search failed in query_by_memory_type — "
                "falling back to substring scan",
                exc_info=True,
            )
            semantic_hits = await store.search_notes(
                config, user_id, query, limit=max(capped * 3, 30),
            )

    seen: set[str] = set()
    composed: list[dict] = []

    async def _ensure_type(note):
        if note.get("memory_type"):
            return note
        mt_value = await _memory_type_of(config, user_id, note["id"])
        out = dict(note)
        out["memory_type"] = mt_value
        return out

    for note in semantic_hits:
        if note["id"] in seen:
            continue
        note = await _ensure_type(note)
        if not _matches_type(note.get("memory_type"), types_filter):
            continue
        seen.add(note["id"])
        composed.append(note)
        if len(composed) >= capped:
            return composed

    for note_id in wikilink_ids:
        if note_id in seen:
            continue
        note = await store.get_note(config, user_id, note_id)
        if note is None:
            continue
        note = await _ensure_type(note)
        if not _matches_type(note.get("memory_type"), types_filter):
            continue
        seen.add(note_id)
        composed.append(note)
        if len(composed) >= capped:
            return composed

    if types_filter and len(composed) < capped:
        async with db_session(config) as db:
            placeholders = ",".join("?" * len(types_filter))
            rows = await db.execute(
                f"SELECT id FROM notes "
                f"WHERE user_id = ? "
                f"AND memory_type IN ({placeholders}) "
                f"ORDER BY pinned DESC, created_at DESC LIMIT ?",
                (user_id, *sorted(types_filter), capped * 3),
            )
            backfill_ids = [r[0] for r in await rows.fetchall() if r[0]]
        for note_id in backfill_ids:
            if note_id in seen:
                continue
            note = await store.get_note(config, user_id, note_id)
            if note is None:
                continue
            note = await _ensure_type(note)
            seen.add(note_id)
            composed.append(note)
            if len(composed) >= capped:
                break

    return composed


async def recall_typed_memory(
    config: Config,
    user_id: str,
    *,
    memory_type: (
        str
        | set[str]
        | frozenset[str]
        | list[str]
        | tuple[str, ...]
        | None
    ) = None,
    topic: str | None = None,
    query: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """High-level recall used by the NL skill.

    Composes :func:`query_by_memory_type` with wikilink-aware ``topic``
    resolution. If ``topic`` resolves to a note, its OWN page is
    prepended to the result list whenever the ``memory_type`` filter
    accepts it. ``query`` flows straight through to the semantic-search
    path. ``memory_type`` filters the final set.

    Validation: at least one of ``memory_type`` / ``topic`` / ``query``
    must be non-empty.
    """
    if (
        memory_type is None
        and not (topic and topic.strip())
        and not (query and query.strip())
    ):
        raise ValueError(
            "recall_typed_memory requires at least one of "
            "memory_type / topic / query"
        )

    topic_note_id: str | None = None
    if topic and topic.strip():
        topic_note = await store.find_by_title(config, user_id, topic)
        topic_note_id = topic_note["id"] if topic_note else None

    raw = await query_by_memory_type(
        config,
        user_id,
        memory_type=memory_type,
        query=query,
        wikilink_target=topic if topic and topic.strip() else None,
        limit=limit,
    )

    if topic_note_id:
        in_raw = any(n["id"] == topic_note_id for n in raw)
        if not in_raw:
            topic_note = await store.get_note(
                config, user_id, topic_note_id,
            )
            if topic_note is not None:
                tn_mt = topic_note.get("memory_type") or (
                    await _memory_type_of(config, user_id, topic_note_id)
                )
                topic_note = dict(topic_note)
                topic_note["memory_type"] = tn_mt
                if _matches_type(
                    tn_mt, _normalize_type_filter(memory_type),
                ):
                    capped = max(1, min(200, int(limit)))
                    raw = ([topic_note] + raw)[:capped]

    return raw


__all__ = [
    "DEFAULT_MEMORY_TYPE",
    "MEMORY_TYPES",
    "query_by_memory_type",
    "recall_typed_memory",
]
