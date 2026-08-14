"""Unified recall fan-out — one skill the brain calls instead of choosing
between three.

Why one skill: the brain previously had to pick between
``recall_memories``, ``lazybrain_search_notes`` and ``lazybrain_ask`` per
turn. The wrong pick = silent miss. This skill fans out to all three
underlying stores in parallel and returns a single ranked, deduped list
where every row is labelled ``[source, conf, last_verified]`` so the
brain can reason about confidence without guessing.

Sources merged:
  • personal_memory   — substring match on legacy facts table
  • lazybrain_notes   — semantic + substring (Ollama-backed, fallback safe)
  • lazybrain_lessons — kind/shape cards filtered to outcome ∈ {verified, pending}

Hard rules:
  • Never raise. Ollama down → semantic skipped, substring still works.
  • Dedup by content hash (folded) so the same fact doesn't surface
    twice as both a personal_memory row and its LazyBrain mirror.
  • Keep existing skill names ``recall_memories`` and
    ``lazybrain_search_notes`` working — they delegate to this code path.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import unicodedata

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# ── Source labels used in every emitted row ────────────────────────────
SRC_PERSONAL = "memory"
SRC_NOTE = "note"
SRC_LESSON = "lesson"


class MemoryRecallSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def read_only(self) -> bool:
        return True

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "recall_memories"

    @property
    def description(self) -> str:
        return (
            "Search EVERYTHING the user's brain knows in one call: legacy "
            "personal facts + LazyBrain notes (semantic + substring) + "
            "verified skill lessons. Each result is labelled with its "
            "source and confidence so you can reason about staleness. Use "
            "this whenever you suspect the user (or a past skill run) "
            "already told you something."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to search for. Substring + semantic match, "
                        "case-insensitive."
                    ),
                },
                "scope": {
                    "type": "string",
                    "enum": ["default", "skills_vault", "all"],
                    "default": "default",
                    "description": (
                        "default = facts + notes + verified lessons. "
                        "skills_vault = include archived/legacy skill shapes. "
                        "all = everything, including superseded/contested."
                    ),
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Memory system not configured"

        query = ((params or {}).get("query") or "").strip()
        if not query:
            return "Error: `query` is required."
        scope = (params or {}).get("scope") or "default"

        # Fan out the legacy personal store + ONE LazyBrain semantic_search
        # in parallel. The single semantic call feeds BOTH the note stream
        # and the lesson stream — duplicating it doubled Ollama embeds +
        # vault decrypts on every recall.
        personal_task = _safe_personal_search(self._config, user_id, query)
        lb_task = _safe_lazybrain_combined_search(
            self._config, user_id, query, scope=scope,
        )

        personal, (notes, lessons) = await asyncio.gather(
            personal_task, lb_task,
        )

        merged = _merge_and_dedupe(personal, notes, lessons)

        if merged:
            return _format_unified_hits(query, merged, scope=scope)

        # All three empty — fall back to a preview so the brain sees
        # what's stored before it gives up.
        from lazyclaw.memory.personal import get_memories

        vault_keys = await _vault_keys_safe(self._config, user_id)
        all_personal = await get_memories(self._config, user_id, limit=8)
        all_lb = await _safe_recent_lb_notes(self._config, user_id, limit=8)

        if not all_personal and not all_lb:
            base = f"No direct match for '{query}' and no memories stored yet."
            if vault_keys:
                return (
                    f"{base} Vault contains {len(vault_keys)} credentials "
                    f"(names only): {', '.join(vault_keys)}. "
                    f"If the user is asking about a credential, call "
                    f"`vault_get(key=...)` instead of retrying recall_memories."
                )
            return f"{base} Nothing to search."

        return _format_no_match_preview(
            query, all_personal, all_lb, vault_keys,
        )


# ── Source-specific safe wrappers ──────────────────────────────────────


async def _safe_personal_search(config, user_id: str, query: str) -> list[dict]:
    """Substring on legacy personal_memory. Returns labelled rows."""
    try:
        from lazyclaw.memory.personal import search_memories
        hits = await search_memories(config, user_id, query)
    except Exception:
        logger.debug("personal search failed", exc_info=True)
        return []
    out: list[dict] = []
    for m in hits:
        out.append({
            "source": SRC_PERSONAL,
            "id": m.get("id"),
            "title": (m.get("type") or m.get("memory_type") or "fact"),
            "content": m.get("content") or "",
            "conf": int(m.get("importance") or 5),
            "last_verified": None,  # personal_memory has no verification log
            "tags": [],
        })
    return out


async def _safe_lazybrain_combined_search(
    config, user_id: str, query: str, *, scope: str = "default",
) -> tuple[list[dict], list[dict]]:
    """Fetch notes + lesson shapes from LazyBrain in ONE pair of calls.

    Returns ``(notes, lessons)``. We issue two ``semantic_search`` calls:
    one general (post-filtered to user-facing notes) and one tag-prefiltered
    to ``kind/shape`` so the verified-shape pool actually returns hits
    instead of post-filtering top-8 generic results to empty.
    """
    try:
        from lazyclaw.lazybrain import embeddings as lb_embeddings
        from lazyclaw.lazybrain.store import is_user_facing_memory_note
    except Exception:
        logger.debug("LazyBrain import failed", exc_info=True)
        return [], []

    # Run both retrievals concurrently — same query, different tag scope.
    general_task = _semantic_safe(
        lb_embeddings, config, user_id, query, k=8, tag_prefix=None,
    )
    shape_task = _semantic_safe(
        lb_embeddings, config, user_id, query, k=16, tag_prefix="kind/shape",
    )
    general_raw, shape_raw = await asyncio.gather(general_task, shape_task)

    notes: list[dict] = []
    for n in general_raw:
        if not is_user_facing_memory_note(n):
            continue
        notes.append({
            "source": SRC_NOTE,
            "id": f"lb:{n['id']}",
            "title": n.get("title") or "(untitled)",
            "content": n.get("content") or "",
            "conf": int(n.get("importance") or 5),
            "last_verified": n.get("updated_at"),
            "tags": list(n.get("tags") or []),
            "_score": n.get("_score"),
        })

    if scope == "default":
        wanted_outcomes = {"verified", "pending"}
    elif scope == "skills_vault":
        wanted_outcomes = {"verified", "pending", "superseded"}
    else:  # "all"
        wanted_outcomes = {"verified", "pending", "superseded", "failed"}

    lessons: list[dict] = []
    for n in shape_raw:
        tags = [str(t) for t in (n.get("tags") or [])]
        # tag_prefix prefilter is JSON-substring LIKE — re-check exact match
        # here so kind/shape-failed (if it ever shows up) can't slip in.
        if "kind/shape" not in tags:
            continue
        if "kind/known-bad" in tags:
            continue
        outcome = next(
            (t.split("/", 1)[1] for t in tags if t.startswith("outcome/")),
            None,
        )
        if outcome not in wanted_outcomes:
            continue
        lessons.append({
            "source": SRC_LESSON,
            "id": f"lb:{n['id']}",
            "title": n.get("title") or "(untitled lesson)",
            "content": n.get("content") or "",
            "conf": int(n.get("importance") or 5),
            "last_verified": n.get("updated_at"),
            "tags": tags,
            "outcome": outcome,
            "_score": n.get("_score"),
        })
    return notes, lessons


async def _semantic_safe(
    lb_embeddings, config, user_id: str, query: str, *, k: int,
    tag_prefix: str | None,
) -> list[dict]:
    """Wrap semantic_search so a single failure doesn't poison the gather."""
    try:
        kwargs: dict = {"k": k}
        if tag_prefix:
            kwargs["tag_prefix"] = tag_prefix
        result = await lb_embeddings.semantic_search(
            config, user_id, query, **kwargs,
        )
    except TypeError:
        # Older signature without tag_prefix kwarg → retry without it.
        try:
            result = await lb_embeddings.semantic_search(
                config, user_id, query, k=k,
            )
        except Exception:
            logger.debug("semantic_search raised on retry", exc_info=True)
            return []
    except Exception:
        logger.debug("semantic_search raised", exc_info=True)
        return []
    raw = result.get("results") if isinstance(result, dict) else None
    return list(raw or [])


async def _safe_recent_lb_notes(config, user_id: str, limit: int) -> list[dict]:
    """Recent user-facing LazyBrain notes for the no-match preview."""
    try:
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.lazybrain.store import is_user_facing_memory_note
    except Exception:
        return []
    try:
        notes = await lb_store.list_notes(config, user_id, limit=limit * 3)
    except Exception:
        return []
    filtered = [n for n in notes if is_user_facing_memory_note(n)]
    return filtered[:limit]


async def _vault_keys_safe(config, user_id: str) -> list[str]:
    try:
        from lazyclaw.crypto.vault import list_credentials
        return await list_credentials(config, user_id)
    except Exception:
        return []


# ── Merge + dedup ──────────────────────────────────────────────────────


def _content_hash(text: str) -> str:
    """Stable hash over folded content — case + accent + whitespace
    insensitive so a personal_memory row and its LazyBrain mirror
    collapse into one. SHA-1 is fine; we want speed not crypto."""
    if not text:
        return ""
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(c)
    )
    return hashlib.sha1(" ".join(folded.split()).encode()).hexdigest()


def _merge_and_dedupe(
    personal: list[dict], notes: list[dict], lessons: list[dict],
) -> list[dict]:
    """Combine the three source streams into one ranked list.

    Source priority on tie: lesson > note > personal — verified shapes
    are higher signal than substring memory hits. Within a source we
    preserve the upstream order (already cosine-ranked or
    importance-ranked).
    """
    merged: list[dict] = []
    seen: set[str] = set()
    # lessons first so their content hash claims the slot before a
    # near-duplicate note rolls in.
    for stream in (lessons, notes, personal):
        for row in stream:
            h = _content_hash(row.get("content") or row.get("title") or "")
            if h and h in seen:
                continue
            if h:
                seen.add(h)
            merged.append(row)
    # Combined rank: cosine similarity carries more weight than the
    # importance integer so a high-relevance note (score 0.9) outranks a
    # mid-importance substring hit (importance 6, no score). Without the
    # *2 multiplier importance dominated and recall quality regressed.
    merged.sort(
        key=lambda r: -(
            int(r.get("conf") or 5)
            + float(r.get("_score") or 0.0) * 2.0
        )
    )
    # Lesson cards (verified shapes, stored at importance 7) structurally
    # outrank user facts (default 5) AND arrive k=16 vs k=8 — unchecked,
    # a query returned 17/19 agent skill recipes and buried the user's
    # actual memories (2026-08-14 "doesn't know anything" audit). Cap
    # lessons at 4 of the final 20; facts/notes fill the rest.
    _MAX_LESSON_ROWS = 4
    capped: list[dict] = []
    lesson_rows = 0
    for row in merged:
        if row.get("source") == "lesson":
            if lesson_rows >= _MAX_LESSON_ROWS:
                continue
            lesson_rows += 1
        capped.append(row)
        if len(capped) >= 20:
            break
    return capped


# ── Formatting ─────────────────────────────────────────────────────────


def _row_label(row: dict) -> str:
    """Render the [source:conf ✓verified DATE] tag the brain reads."""
    src = row.get("source") or "?"
    conf = int(row.get("conf") or 5)
    last = (row.get("last_verified") or "")[:10]
    if row.get("source") == SRC_LESSON:
        outcome = row.get("outcome") or "pending"
        mark = "✓" if outcome == "verified" else "?"
        return f"[{src}:{conf} {mark} {outcome} {last}]"
    if last:
        return f"[{src}:{conf} ✓ {last}]"
    return f"[{src}:{conf}]"


def _format_unified_hits(query: str, rows: list[dict], scope: str) -> str:
    lines: list[str] = []
    lines.append(
        f"Matches for '{query}' ({len(rows)}, scope={scope}):"
    )
    for row in rows:
        label = _row_label(row)
        title = (row.get("title") or "(untitled)").strip()
        body = (row.get("content") or "").strip().replace("\n", " ")[:200]
        rid = row.get("id") or ""
        lines.append(f"- {label} **{title}** — {body} (id: {rid})")
    return "\n".join(lines)


def _format_no_match_preview(
    query: str,
    personal: list[dict],
    lb: list[dict],
    vault_keys: list[str],
) -> str:
    lines: list[str] = []
    lines.append(
        f"No direct match for '{query}'. Here's what IS stored — "
        f"the answer might be phrased differently:"
    )
    if personal:
        lines.append("")
        lines.append("**Personal facts (most recent):**")
        for m in personal:
            mtype = m.get("type") or m.get("memory_type") or "?"
            lines.append(f"- [{mtype}] {m['content']} (id: {m['id']})")
    if lb:
        lines.append("")
        lines.append("**LazyBrain notes (most recent):**")
        for n in lb:
            title = (n.get("title") or "(untitled)").strip()
            content = (n.get("content") or "").strip().replace("\n", " ")
            lines.append(f"- **{title}** — {content[:160]} (id: lb:{n['id']})")
    if vault_keys:
        lines.append("")
        lines.append(
            f"Vault keys (names only, values encrypted): "
            f"{', '.join(vault_keys)}"
        )
        lines.append(
            "If the user is asking about a credential/API key/OAuth secret, "
            "call `vault_get(key=...)` — credentials are NEVER in personal memory."
        )
    lines.append("")
    lines.append(
        "STOP. Do not retry recall_memories with other keywords — every "
        "store is searched on every call. If the fact isn't above, ask "
        "the user instead of guessing."
    )
    return "\n".join(lines)
