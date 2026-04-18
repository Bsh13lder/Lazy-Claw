"""Site Memory — Encrypted per-domain browser knowledge.

Extracted from LazyTasker. Learns from successful browser interactions
and recalls knowledge for future visits to the same domain.
"""

from __future__ import annotations

import json
import logging
import uuid
from urllib.parse import urlparse

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# Valid memory types
MEMORY_TYPES = frozenset({
    "login_flow",
    "navigation",
    "form_map",
    "site_structure",
    "site_research",
    "cookie_note",
    "custom",
    "site_learning",
    "compiled_path",
})


async def remember(
    config: Config,
    user_id: str,
    url: str,
    memory_type: str,
    title: str,
    content: dict,
) -> str:
    """Save encrypted site memory. Uses UPSERT — updates if same domain+type+title exists.

    Returns the memory ID.
    """
    if memory_type not in MEMORY_TYPES:
        raise ValueError(f"Invalid memory type: {memory_type}. Must be one of {MEMORY_TYPES}")

    domain = urlparse(url).hostname or url
    key = await get_user_dek(config, user_id)
    enc_title = encrypt_field(title, key)
    enc_content = encrypt_field(json.dumps(content), key)
    memory_id = str(uuid.uuid4())

    async with db_session(config) as db:
        # Check for existing memory with same domain + type + encrypted title
        rows = await db.execute_fetchall(
            "SELECT id, title FROM site_memory WHERE user_id = ? AND domain = ? AND memory_type = ?",
            (user_id, domain, memory_type),
        )
        existing_id = None
        for row in rows:
            decrypted_title = decrypt_field(row["title"], key)
            if decrypted_title == title:
                existing_id = row["id"]
                break

        if existing_id:
            await db.execute(
                "UPDATE site_memory SET content = ?, success_count = success_count + 1, "
                "last_used = datetime('now'), updated_at = datetime('now') "
                "WHERE id = ?",
                (enc_content, existing_id),
            )
            await db.commit()
            logger.info("Updated site memory %s for %s", existing_id, domain)
            memory_id = existing_id
        else:
            await db.execute(
                "INSERT INTO site_memory (id, user_id, domain, memory_type, title, content, "
                "success_count, fail_count, last_used) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, 0, datetime('now'))",
                (memory_id, user_id, domain, memory_type, enc_title, enc_content),
            )
            await db.commit()
            logger.info("Saved site memory %s for %s (%s)", memory_id, domain, memory_type)

    # Mirror into LazyBrain (agent knowledge) on BOTH insert and update —
    # otherwise repeated captures of the same site knowledge silently skip
    # the PKM and the user never sees anything under "Site knowledge".
    # Fire-and-forget — a PKM failure must not break site memory writes.
    await _mirror_site_memory_note(
        config, user_id, domain, memory_type, title, content,
    )

    return memory_id


async def _mirror_site_memory_note(
    config: Config,
    user_id: str,
    domain: str,
    memory_type: str,
    title: str,
    content: dict,
) -> None:
    """Create or update the LazyBrain mirror note for a site_memory row.

    Uses ``find_by_title`` to avoid creating a new note every time the user
    refreshes the same piece of site knowledge. If a note with the same
    title already exists for this user, we update its content in place;
    otherwise we create a fresh one. Silent on failure.
    """
    try:
        from lazyclaw.lazybrain import events as lb_events
        from lazyclaw.lazybrain import store as lb_store

        body = (
            f"**Site knowledge:** {title}\n\n"
            f"Domain: `{domain}` — type `{memory_type}`\n\n"
            f"```json\n{json.dumps(content, indent=2)[:1500]}\n```"
        )
        note_title = f"Site: {domain} — {title[:40]}"
        tags = [
            "site-memory", "auto", "owner/agent",
            f"site/{domain}", f"kind/{memory_type}",
        ]

        existing = await lb_store.find_by_title(config, user_id, note_title)
        if existing:
            updated = await lb_store.update_note(
                config, user_id, existing["id"],
                content=body, tags=tags,
            )
            if updated:
                lb_events.publish_note_saved(
                    user_id, updated["id"], updated.get("title"),
                    updated.get("tags"), source="site-memory",
                )
            return

        note = await lb_store.save_note(
            config, user_id,
            content=body, title=note_title, tags=tags, importance=5,
        )
        lb_events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"],
            source="site-memory",
        )
    except Exception:
        logger.debug("lazybrain site_memory mirror failed", exc_info=True)


async def recall(config: Config, user_id: str, url: str) -> dict[str, list[dict]]:
    """Get decrypted site memories for a domain.

    Returns dict grouped by memory_type.
    """
    domain = urlparse(url).hostname or url
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        rows = await db.execute_fetchall(
            "SELECT id, memory_type, title, content, success_count, fail_count "
            "FROM site_memory WHERE user_id = ? AND domain = ? "
            "ORDER BY success_count DESC, last_used DESC LIMIT 20",
            (user_id, domain),
        )

        # Update last_used for recalled memories
        if rows:
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            await db.execute(
                f"UPDATE site_memory SET last_used = datetime('now') WHERE id IN ({placeholders})",
                ids,
            )
            await db.commit()

    memories: dict[str, list[dict]] = {}
    for row in rows:
        mem_type = row["memory_type"]
        title = decrypt_field(row["title"], key)
        content_str = decrypt_field(row["content"], key)
        try:
            content = json.loads(content_str) if content_str else {}
        except json.JSONDecodeError:
            logger.debug("Failed to parse site memory content as JSON for %s", row["id"], exc_info=True)
            content = {"raw": content_str}

        entry = {
            "id": row["id"],
            "title": title,
            "content": content,
            "success_count": row["success_count"],
            "fail_count": row["fail_count"],
        }
        memories.setdefault(mem_type, []).append(entry)

    return memories


async def recall_all(config: Config, user_id: str) -> list[dict]:
    """Get all site memories for a user (management UI)."""
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        rows = await db.execute_fetchall(
            "SELECT id, domain, memory_type, title, success_count, fail_count, "
            "last_used, created_at FROM site_memory WHERE user_id = ? "
            "ORDER BY last_used DESC",
            (user_id,),
        )

    result = []
    for row in rows:
        result.append({
            "id": row["id"],
            "domain": row["domain"],
            "memory_type": row["memory_type"],
            "title": decrypt_field(row["title"], key),
            "success_count": row["success_count"],
            "fail_count": row["fail_count"],
            "last_used": row["last_used"],
            "created_at": row["created_at"],
        })
    return result


async def forget(config: Config, user_id: str, memory_id: str) -> bool:
    """Delete a specific site memory."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM site_memory WHERE id = ? AND user_id = ?",
            (memory_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def forget_domain(config: Config, user_id: str, domain: str) -> int:
    """Delete all site memories for a domain. Returns count deleted."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM site_memory WHERE user_id = ? AND domain = ?",
            (user_id, domain),
        )
        await db.commit()
        return cursor.rowcount


async def forget_all(config: Config, user_id: str) -> int:
    """Delete all site memories for a user. Returns count deleted."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM site_memory WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()
        return cursor.rowcount


async def mark_failed(
    config: Config, user_id: str, url: str, memory_type: str, title: str
) -> None:
    """Increment fail count for a memory. Auto-deletes if fail > success + 2."""
    domain = urlparse(url).hostname or url
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        rows = await db.execute_fetchall(
            "SELECT id, title FROM site_memory WHERE user_id = ? AND domain = ? AND memory_type = ?",
            (user_id, domain, memory_type),
        )
        target_id = None
        for row in rows:
            if decrypt_field(row["title"], key) == title:
                target_id = row["id"]
                break

        if not target_id:
            return

        await db.execute(
            "UPDATE site_memory SET fail_count = fail_count + 1 WHERE id = ?",
            (target_id,),
        )
        # Auto-cleanup: delete if fail_count > success_count + 2
        await db.execute(
            "DELETE FROM site_memory WHERE id = ? AND fail_count > success_count + 2",
            (target_id,),
        )
        await db.commit()


def format_memories_for_context(memories: dict[str, list[dict]]) -> str:
    """Format site memories as readable text for agent prompt injection."""
    if not memories:
        return ""

    type_labels = {
        "login_flow": "Login Flow",
        "navigation": "Navigation Shortcuts",
        "form_map": "Form Fields",
        "site_structure": "Site Structure",
        "cookie_note": "Notes",
        "custom": "Other",
        "site_learning": "Auto-Learned",
    }

    sections = []
    for mem_type, entries in memories.items():
        label = type_labels.get(mem_type, mem_type.replace("_", " ").title())
        lines = [f"### {label}"]
        for entry in entries:
            lines.append(f"- {entry['title']}")
            if isinstance(entry["content"], dict):
                for k, v in entry["content"].items():
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(f"  {entry['content']}")
        sections.append("\n".join(lines))

    return "## Site Knowledge\n\n" + "\n\n".join(sections)
