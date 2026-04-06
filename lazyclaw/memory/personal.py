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
) -> str:
    """Save a memory. Returns the memory ID."""
    key = await get_user_dek(config, user_id)
    memory_id = str(uuid4())
    encrypted = encrypt(content, key)
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO personal_memory (id, user_id, memory_type, content, importance) "
            "VALUES (?, ?, ?, ?, ?)",
            (memory_id, user_id, memory_type, encrypted, importance),
        )
        await db.commit()
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
