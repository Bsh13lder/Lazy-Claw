"""Contact management skills — unified person store across channels.

Sits in front of the WhatsApp / Instagram / email channel layers so the
brain can resolve a name like "Buchvardi" to a verified phone number,
Instagram handle, or email before invoking a channel-specific skill.

Five skills:
- sync_macos_contacts — pull macOS Contacts.app via JXA, manual edits win
- find_contact         — fuzzy lookup (name / alias / handle value)
- save_contact         — manual add (works in any channel)
- update_contact       — patch handles, aliases, notes
- list_contacts        — overview
"""

from __future__ import annotations

import json
import logging
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class SyncMacOSContactsSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "sync_macos_contacts"

    @property
    def display_name(self) -> str:
        return "sync macOS contacts"

    @property
    def description(self) -> str:
        return (
            "Pull every contact from macOS Contacts.app into LazyClaw's "
            "encrypted contact store. Manual edits made via update_contact "
            "are preserved on re-sync. First run requires Contacts permission "
            "in System Settings → Privacy & Security → Contacts."
        )

    @property
    def category(self) -> str:
        return "contacts"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            from lazyclaw.config import load_config
            self._config = load_config()

        from lazyclaw.contacts.macos_sync import sync_from_macos

        result = await sync_from_macos(self._config, user_id)
        if result.error:
            return f"Sync failed: {result.error}"

        return (
            f"Synced {result.total_in_address_book} contacts from macOS Contacts. "
            f"Created {result.created}, updated {result.updated}, "
            f"unchanged {result.unchanged}, skipped {result.skipped}."
        )


# ---------------------------------------------------------------------------
# Find
# ---------------------------------------------------------------------------


class FindContactSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "find_contact"

    @property
    def display_name(self) -> str:
        return "find contact"

    @property
    def description(self) -> str:
        return (
            "Look up a person in the contact store by name, nickname, or "
            "any handle (phone digits, email, Instagram). Returns the top "
            "matches with phone / email / Instagram / WhatsApp JID. ALWAYS "
            "call this before whatsapp_send / instagram_send / email_send "
            "when the user names a person — never compose a phone number "
            "from past conversation digits."
        )

    @property
    def category(self) -> str:
        return "contacts"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Name, nickname, partial phone, email, or Instagram handle.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5).",
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            from lazyclaw.config import load_config
            self._config = load_config()

        from lazyclaw.contacts.store import find_contact

        query = (params.get("query") or "").strip()
        if not query:
            return "Error: query is required."
        limit = int(params.get("limit") or 5)

        matches = await find_contact(self._config, user_id, query, limit=limit)
        if not matches:
            return (
                f"No contact found matching '{query}'. ASK the user for "
                "the correct name OR full international phone number "
                "(e.g. +34XXXXXXXXX) — do NOT guess."
            )

        return json.dumps(
            [m.to_public_dict() for m in matches], ensure_ascii=False, indent=2
        )


# ---------------------------------------------------------------------------
# Save (manual add)
# ---------------------------------------------------------------------------


class SaveContactSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "save_contact"

    @property
    def display_name(self) -> str:
        return "save contact"

    @property
    def description(self) -> str:
        return (
            "Manually save a person to the contact store. Use this when the "
            "user provides handles directly (e.g. 'save Buchvardi's Instagram "
            "as @buchvardi' or 'Buchvardi's number is +34641952564'). "
            "Multiple phones / emails per contact supported."
        )

    @property
    def category(self) -> str:
        return "contacts"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Display name (required).",
                },
                "phone": {
                    "type": "string",
                    "description": "Full international phone number, e.g. +34641952564. Country code REQUIRED.",
                },
                "email": {"type": "string"},
                "instagram": {
                    "type": "string",
                    "description": "Instagram username, with or without leading @.",
                },
                "telegram": {"type": "string"},
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Nicknames or alternate spellings.",
                },
                "notes": {"type": "string"},
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            from lazyclaw.config import load_config
            self._config = load_config()

        from lazyclaw.contacts.store import create_contact

        name = (params.get("name") or "").strip()
        if not name:
            return "Error: name is required."

        handles: dict[str, Any] = {}
        phone = (params.get("phone") or "").strip()
        if phone:
            if not phone.startswith("+"):
                return (
                    f"Refusing to save bare number '{phone}'. Country code "
                    "is required (e.g. +34641952564). Ask the user to "
                    "include the country code, then retry."
                )
            handles["phone"] = [phone]
        email = (params.get("email") or "").strip()
        if email:
            handles["email"] = [email]
        instagram = (params.get("instagram") or "").strip()
        if instagram:
            handles["instagram"] = instagram if instagram.startswith("@") else f"@{instagram}"
        telegram = (params.get("telegram") or "").strip()
        if telegram:
            handles["telegram"] = telegram if telegram.startswith("@") else f"@{telegram}"

        record = await create_contact(
            self._config,
            user_id,
            name=name,
            aliases=params.get("aliases") or [],
            handles=handles,
            notes=params.get("notes"),
            source="manual",
        )
        return f"Saved contact '{record.name_canonical}' (id {record.id}). Handles: {handles or '{}'}"


# ---------------------------------------------------------------------------
# Update (patch existing)
# ---------------------------------------------------------------------------


class UpdateContactSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "update_contact"

    @property
    def display_name(self) -> str:
        return "update contact"

    @property
    def description(self) -> str:
        return (
            "Patch a contact — add a handle, a new alias, or update notes. "
            "Resolves the contact via fuzzy lookup (same as find_contact). "
            "Manual edits made here ALWAYS win over future macOS re-syncs."
        )

    @property
    def category(self) -> str:
        return "contacts"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Name / nickname / handle to find the contact to update.",
                },
                "set_handle_key": {
                    "type": "string",
                    "description": "Handle key to set (instagram, telegram, whatsapp_jid, phone, email).",
                },
                "set_handle_value": {
                    "type": "string",
                    "description": "Value for the handle key.",
                },
                "add_alias": {"type": "string"},
                "rename": {
                    "type": "string",
                    "description": "New display name.",
                },
                "notes": {"type": "string"},
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            from lazyclaw.config import load_config
            self._config = load_config()

        from lazyclaw.contacts.store import find_contact, update_contact

        query = (params.get("query") or "").strip()
        if not query:
            return "Error: query is required."

        matches = await find_contact(self._config, user_id, query, limit=2)
        if not matches:
            return f"No contact matched '{query}'. Use save_contact to create a new one."
        if len(matches) > 1 and matches[0].name_canonical != matches[1].name_canonical:
            names = ", ".join(m.name_canonical for m in matches[:3])
            return (
                f"Ambiguous: '{query}' matched {names}. Re-run with a more "
                "specific name or the full phone number."
            )

        target = matches[0]
        kwargs: dict[str, Any] = {}
        h_key = (params.get("set_handle_key") or "").strip()
        h_val = (params.get("set_handle_value") or "").strip()
        if h_key and h_val:
            # Phone handle stored as a list to support multiples.
            if h_key == "phone":
                if not h_val.startswith("+"):
                    return f"Refusing to save bare number '{h_val}' — country code required."
                phones = list(target.handles.get("phone") or [])
                if h_val not in phones:
                    phones.append(h_val)
                kwargs["set_handle"] = ("phone", phones)
            elif h_key == "email":
                emails = list(target.handles.get("email") or [])
                if h_val not in emails:
                    emails.append(h_val)
                kwargs["set_handle"] = ("email", emails)
            elif h_key in {"instagram", "telegram"}:
                v = h_val if h_val.startswith("@") else f"@{h_val}"
                kwargs["set_handle"] = (h_key, v)
            else:
                kwargs["set_handle"] = (h_key, h_val)

        if params.get("add_alias"):
            kwargs["add_alias"] = params["add_alias"].strip()
        if params.get("rename"):
            kwargs["name"] = params["rename"].strip()
        if params.get("notes") is not None:
            kwargs["notes"] = params["notes"]

        if not kwargs:
            return "Nothing to update — pass at least one of set_handle_key/value, add_alias, rename, notes."

        updated = await update_contact(self._config, user_id, target.id, **kwargs)
        if updated is None:
            return f"Contact '{target.name_canonical}' disappeared mid-update."
        return (
            f"Updated '{updated.name_canonical}'. Handles now: "
            f"{json.dumps(updated.handles, ensure_ascii=False)}"
        )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class ListContactsSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_contacts"

    @property
    def display_name(self) -> str:
        return "list contacts"

    @property
    def description(self) -> str:
        return (
            "Show contacts from the encrypted store. Optional filter on "
            "source (manual / macos_contacts) and limit. Use find_contact "
            "for targeted lookup; this is for browsing."
        )

    @property
    def category(self) -> str:
        return "contacts"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["manual", "macos_contacts"],
                    "description": "Filter by source.",
                },
                "limit": {"type": "integer", "description": "Default 50."},
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            from lazyclaw.config import load_config
            self._config = load_config()

        from lazyclaw.contacts.store import list_contacts

        limit = int(params.get("limit") or 50)
        source_filter = params.get("source")

        contacts = await list_contacts(self._config, user_id, limit=limit * 2 if source_filter else limit)
        if source_filter:
            contacts = [c for c in contacts if c.source == source_filter][:limit]

        if not contacts:
            return "No contacts in store. Run sync_macos_contacts or save_contact."

        summary = [
            {
                "name": c.name_canonical,
                "source": c.source,
                "handles": c.handles,
                "aliases": c.aliases or None,
            }
            for c in contacts
        ]
        return json.dumps(summary, ensure_ascii=False, indent=2)
