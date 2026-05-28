from __future__ import annotations
import logging
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
    """Search memories by substring match on decrypted content."""
    all_memories = await get_memories(config, user_id, limit=100)
    query_lower = query.lower()
    matches = [m for m in all_memories if query_lower in m["content"].lower()]
    return matches[:limit]
