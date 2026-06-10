"""Name an inbox contact — one action, three stores.

When the user names a thread's contact ("this is Maria"), the name must
land everywhere the platform knows people:

  1. the thread itself (``channel_threads.contact_name``, encrypted) — the
     inbox shows the name immediately;
  2. the unified contact store (``contacts``) — handles (JID / phone /
     email / username) attach to the person so ``find_contact`` and every
     messaging send resolve them;
  3. a LazyBrain reference note titled with the name — the person becomes a
     ``[[wikilink]]`` node in the knowledge graph.

Every step is best-effort after the first: a LazyBrain hiccup must never
lose the rename.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from lazyclaw.comms import thread_store

logger = logging.getLogger(__name__)

# channel -> contact-store handle key
_HANDLE_KEYS = {
    "whatsapp": "whatsapp_jid",
    "email": "email",
    "instagram": "instagram",
    "telegram": "telegram",
}


def _phone_from_jid(handle: str) -> str | None:
    """``34611222333@s.whatsapp.net`` → ``+34611222333`` (direct chats only)."""
    m = re.match(r"^(\d{7,15})@s\.whatsapp\.net$", handle or "")
    return f"+{m.group(1)}" if m else None


async def name_thread_contact(
    config: Any, user_id: str, thread: dict, name: str,
) -> dict:
    """Apply ``name`` to the thread's contact across all three stores.

    Returns ``{"thread_renamed": bool, "contact_id": str | None,
    "contact_created": bool, "note_id": str | None}``.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name must not be empty")

    channel = thread.get("channel") or ""
    handle = thread.get("contact_handle") or ""

    # 1. Thread display name — the rename the user actually sees.
    renamed = await thread_store.set_thread_contact_name(
        config, user_id, thread["id"], name,
    )

    # 2. Unified contact store — attach every handle we know to the person.
    contact_id: str | None = None
    created = False
    handles: dict[str, Any] = {}
    handle_key = _HANDLE_KEYS.get(channel)
    if handle_key and handle:
        handles[handle_key] = handle
    phone = _phone_from_jid(handle) if channel == "whatsapp" else None
    if phone:
        handles["phone"] = [phone]
    try:
        from lazyclaw.contacts import store as contacts_store

        # Match by the handle first (strongest signal), then by the name.
        matches = []
        if phone or handle:
            matches = await contacts_store.find_contact(
                config, user_id, phone or handle, limit=1,
            )
        if not matches:
            matches = await contacts_store.find_contact(
                config, user_id, name, limit=1,
            )
        if matches:
            contact = await contacts_store.update_contact(
                config, user_id, matches[0].id,
                name=name,
                handles={**matches[0].handles, **handles},
            )
            contact_id = contact.id if contact else matches[0].id
        else:
            contact = await contacts_store.create_contact(
                config, user_id, name, handles=handles, source="inbox",
            )
            contact_id = contact.id
            created = True
    except Exception:
        logger.warning("contact store upsert failed for rename", exc_info=True)

    # 3. LazyBrain reference note — the [[Name]] wikilink page.
    note_id: str | None = None
    try:
        from lazyclaw.lazybrain import store as lb_store

        handle_lines = "\n".join(
            f"- {k}: {v}" for k, v in handles.items()
        ) or f"- {channel}: (handle on file)"
        note = await lb_store.save_note(
            config, user_id,
            content=(
                f"Contact reached via {channel} — named from the unified "
                f"inbox.\n\n**Handles**\n{handle_lines}\n\n"
                f"Conversations: [[{name}]] threads live in the Inbox."
            ),
            title=name,
            tags=["contact", channel],
            memory_type="reference",
        )
        note_id = note.get("id")
    except Exception:
        logger.warning("lazybrain contact note failed for rename", exc_info=True)

    return {
        "thread_renamed": renamed,
        "contact_id": contact_id,
        "contact_created": created,
        "note_id": note_id,
    }
