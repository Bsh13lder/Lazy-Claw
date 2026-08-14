from __future__ import annotations
import logging
import re
from uuid import uuid4
from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


async def save_memory(
    config: Config,
    user_id: str,
    content: str,
    memory_type: str = "fact",
    importance: int = 5,
    owner: str = "user",
) -> str:
    """Save a memory. Returns the memory ID.

    Always writes to LazyBrain as ``#memory #owner/{owner} #kind/{type}``
    so the user sees every fact/preference in the PKM. By default ALSO
    writes to the legacy ``personal_memory`` table for back-compat.

    When ``config.memory_unified`` is True (env: ``MEMORY_UNIFIED=1``),
    the legacy INSERT is skipped — LazyBrain becomes the sole source
    of truth. Reads in ``recall_memories`` and ``context_builder`` keep
    merging both stores, so existing rows stay accessible during the
    transition. Run ``cli_migrate_lazybrain.py`` once before flipping
    the flag to copy any orphan rows.
    """
    key = await get_user_dek(config, user_id)
    memory_id = str(uuid4())

    if not getattr(config, "memory_unified", False):
        encrypted = encrypt(content, key)
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO personal_memory (id, user_id, memory_type, content, importance) "
                "VALUES (?, ?, ?, ?, ?)",
                (memory_id, user_id, memory_type, encrypted, importance),
            )
            await db.commit()

    try:
        from lazyclaw.lazybrain import events as lb_events
        from lazyclaw.lazybrain import store as lb_store

        # Map legacy personal_memory type strings to taxonomy values so the
        # explicit memory_type kwarg wins over the classifier (store.py:561).
        _LEGACY_TYPE_MAP: dict[str, str] = {
            "preference": "user",
            "context": "project",
        }
        lb_memory_type: str = _LEGACY_TYPE_MAP.get(memory_type, "fact")

        note = await lb_store.save_note(
            config,
            user_id,
            content=content,
            title=f"{memory_type.capitalize()}: {content[:60]}",
            tags=["memory", "auto", f"owner/{owner}", f"kind/{memory_type}"],
            importance=importance,
            memory_type=lb_memory_type,
        )
        lb_events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"], source="memory",
        )
        # In unified mode, return the LazyBrain id so callers (and the
        # `recall_memories` no-match preview) can dereference it.
        if getattr(config, "memory_unified", False):
            memory_id = f"lb:{note['id']}"
    except Exception:
        logger.warning(
            "lazybrain memory mirror failed for user %s", user_id, exc_info=True,
        )

    return memory_id


async def get_memories(
    config: Config, user_id: str, limit: int = 20
) -> list[dict]:
    """Get memories ordered by importance desc. Returns list of dicts with id, type, content, importance."""
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, memory_type, content, importance, created_at FROM personal_memory "
            "WHERE user_id = ? ORDER BY importance DESC, created_at DESC LIMIT ?",
            (user_id, limit),
        )
        results = await rows.fetchall()

    memories = []
    for row in results:
        decrypted = decrypt_field(row[2], key)
        memories.append({
            "id": row[0],
            "key": row[1],
            "value": decrypted,
            "type": row[1],
            "content": decrypted,
            "importance": row[3],
            "created_at": row[4],
        })
    return memories


async def delete_memory(config: Config, user_id: str, memory_id: str) -> bool:
    """Delete a memory. Returns True if deleted."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM personal_memory WHERE id = ? AND user_id = ?",
            (memory_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def search_memories(
    config: Config, user_id: str, query: str, limit: int = 10
) -> list[dict]:
    """Search memories by token overlap on decrypted content.

    Whole-phrase substring matching made natural-language queries miss
    everything ("what's my Google Workspace email" never appears verbatim
    inside a stored fact) — and since ``save_memory`` notes carry the
    recall-excluded ``memory`` tag, this lane is the ONLY path to
    user-saved facts (2026-08-14 "doesn't know anything" audit). Score by
    overlapping words (len >= 3), keep the exact-substring fast path,
    rank matches-first then importance.
    """
    all_memories = await get_memories(config, user_id, limit=100)
    query_lower = query.lower().strip()
    if not query_lower:
        return []
    q_tokens = {t for t in re.findall(r"[a-z0-9]{3,}", query_lower)}
    scored: list[tuple[int, dict]] = []
    for m in all_memories:
        content_lower = m["content"].lower()
        if query_lower in content_lower:
            scored.append((len(q_tokens) + 1, m))  # exact phrase = top
            continue
        if not q_tokens:
            continue
        m_tokens = {t for t in re.findall(r"[a-z0-9]{3,}", content_lower)}
        overlap = len(q_tokens & m_tokens)
        if overlap >= max(1, min(2, len(q_tokens) - 1)):
            scored.append((overlap, m))
    scored.sort(
        key=lambda x: (x[0], int(x[1].get("importance") or 0)), reverse=True,
    )
    return [m for _, m in scored[:limit]]
