"""Encrypted contact store — CRUD for the contacts table.

One row per real person. Each row carries:
- name_canonical: display name
- aliases: nicknames + transliterations the brain might use ("Bichi", "ბუხვარდი")
- handles: {phone:[E.164], email:[], instagram, telegram, whatsapp_jid, ...}
- notes: free-form
- manual_overrides: keys that survive re-sync from external sources

All PII columns AES-256-GCM. Decrypt-once-into-memory cache per user
keeps name lookup at sub-ms even with thousands of contacts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

ENCRYPTED_FIELDS = frozenset(
    {"name_canonical", "aliases", "handles", "notes", "manual_overrides"}
)

CONTACT_COLUMNS = [
    "id",
    "user_id",
    "name_canonical",
    "aliases",
    "handles",
    "notes",
    "manual_overrides",
    "source",
    "external_id",
    "created_at",
    "updated_at",
]


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class ContactRecord:
    id: str
    user_id: str
    name_canonical: str
    aliases: list[str] = field(default_factory=list)
    handles: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    manual_overrides: dict[str, Any] = field(default_factory=dict)
    source: str = "manual"
    external_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def primary_phone(self) -> str | None:
        phones = self.handles.get("phone") or []
        return phones[0] if phones else None

    def whatsapp_jid(self) -> str | None:
        wa = self.handles.get("whatsapp_jid")
        if wa:
            return wa
        phone = self.primary_phone()
        if phone:
            digits = "".join(c for c in phone if c.isdigit())
            if len(digits) >= 7:
                return f"{digits}@s.whatsapp.net"
        return None

    def to_public_dict(self) -> dict:
        """Decrypted dict safe for the brain to consume."""
        return {
            "id": self.id,
            "name": self.name_canonical,
            "aliases": self.aliases,
            "handles": self.handles,
            "notes": self.notes,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------


def _enc(value: str | None, key: bytes) -> str | None:
    return encrypt(value, key) if value is not None else None


def _enc_json(value: Any, key: bytes) -> str | None:
    if value in (None, [], {}):
        return None
    return encrypt(json.dumps(value, ensure_ascii=False), key)


def _dec_json(value: str | None, key: bytes, fallback: Any) -> Any:
    if value is None:
        return fallback
    plain = decrypt_field(value, key)
    if not plain:
        return fallback
    try:
        return json.loads(plain)
    except (ValueError, TypeError):
        return fallback


def _row_to_record(row, key: bytes) -> ContactRecord:
    cols = {col: row[i] for i, col in enumerate(CONTACT_COLUMNS)}
    return ContactRecord(
        id=cols["id"],
        user_id=cols["user_id"],
        name_canonical=decrypt_field(cols["name_canonical"], key) or "",
        aliases=_dec_json(cols["aliases"], key, []),
        handles=_dec_json(cols["handles"], key, {}),
        notes=decrypt_field(cols["notes"], key) if cols["notes"] else None,
        manual_overrides=_dec_json(cols["manual_overrides"], key, {}),
        source=cols["source"] or "manual",
        external_id=cols["external_id"],
        created_at=cols["created_at"],
        updated_at=cols["updated_at"],
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


SELECT_COLS = ", ".join(CONTACT_COLUMNS)


async def create_contact(
    config: Config,
    user_id: str,
    name: str,
    *,
    aliases: list[str] | None = None,
    handles: dict[str, Any] | None = None,
    notes: str | None = None,
    source: str = "manual",
    external_id: str | None = None,
    manual_overrides: dict[str, Any] | None = None,
) -> ContactRecord:
    key = await get_user_dek(config, user_id)
    contact_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    aliases_list = list(dict.fromkeys(aliases or []))  # de-dupe, preserve order
    handles_dict = handles or {}
    overrides_dict = manual_overrides or {}

    async with db_session(config) as db:
        await db.execute(
            f"INSERT INTO contacts ({SELECT_COLS}) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                contact_id,
                user_id,
                encrypt(name, key),
                _enc_json(aliases_list, key),
                _enc_json(handles_dict, key),
                _enc(notes, key),
                _enc_json(overrides_dict, key),
                source,
                external_id,
                now,
                now,
            ),
        )
        await db.commit()

    return ContactRecord(
        id=contact_id,
        user_id=user_id,
        name_canonical=name,
        aliases=aliases_list,
        handles=handles_dict,
        notes=notes,
        manual_overrides=overrides_dict,
        source=source,
        external_id=external_id,
        created_at=now,
        updated_at=now,
    )


async def get_contact(
    config: Config, user_id: str, contact_id: str
) -> ContactRecord | None:
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {SELECT_COLS} FROM contacts WHERE id = ? AND user_id = ?",
            (contact_id, user_id),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_record(row, key)


async def list_contacts(
    config: Config,
    user_id: str,
    *,
    limit: int | None = None,
) -> list[ContactRecord]:
    key = await get_user_dek(config, user_id)
    sql = (
        f"SELECT {SELECT_COLS} FROM contacts "
        "WHERE user_id = ? ORDER BY updated_at DESC"
    )
    params: tuple = (user_id,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (user_id, limit)

    async with db_session(config) as db:
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
    return [_row_to_record(r, key) for r in rows]


async def delete_contact(
    config: Config, user_id: str, contact_id: str
) -> bool:
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM contacts WHERE id = ? AND user_id = ?",
            (contact_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def update_contact(
    config: Config,
    user_id: str,
    contact_id: str,
    *,
    name: str | None = None,
    aliases: list[str] | None = None,
    handles: dict[str, Any] | None = None,
    notes: str | None = None,
    add_alias: str | None = None,
    set_handle: tuple[str, Any] | None = None,
    mark_manual: bool = True,
) -> ContactRecord | None:
    """Patch a contact. Fields that change are recorded in ``manual_overrides``
    when ``mark_manual=True`` so a future re-sync from macOS won't clobber them.
    """
    current = await get_contact(config, user_id, contact_id)
    if current is None:
        return None

    key = await get_user_dek(config, user_id)
    overrides = dict(current.manual_overrides)

    new_name = current.name_canonical
    if name is not None and name != current.name_canonical:
        new_name = name
        if mark_manual:
            overrides["name"] = name

    new_aliases = list(current.aliases)
    if aliases is not None:
        new_aliases = list(dict.fromkeys(aliases))
        if mark_manual:
            overrides["aliases"] = new_aliases
    if add_alias and add_alias not in new_aliases:
        new_aliases.append(add_alias)
        if mark_manual:
            overrides["aliases"] = new_aliases

    new_handles = dict(current.handles)
    if handles is not None:
        new_handles = handles
        if mark_manual:
            overrides["handles"] = new_handles
    if set_handle is not None:
        h_key, h_val = set_handle
        new_handles[h_key] = h_val
        if mark_manual:
            handle_overrides = overrides.setdefault("handles", {})
            if not isinstance(handle_overrides, dict):
                handle_overrides = {}
                overrides["handles"] = handle_overrides
            handle_overrides[h_key] = h_val

    new_notes = current.notes if notes is None else notes
    if notes is not None and mark_manual:
        overrides["notes"] = notes

    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE contacts SET "
            "name_canonical = ?, aliases = ?, handles = ?, notes = ?, "
            "manual_overrides = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (
                encrypt(new_name, key),
                _enc_json(new_aliases, key),
                _enc_json(new_handles, key),
                _enc(new_notes, key),
                _enc_json(overrides, key),
                now,
                contact_id,
                user_id,
            ),
        )
        await db.commit()

    return ContactRecord(
        id=contact_id,
        user_id=user_id,
        name_canonical=new_name,
        aliases=new_aliases,
        handles=new_handles,
        notes=new_notes,
        manual_overrides=overrides,
        source=current.source,
        external_id=current.external_id,
        created_at=current.created_at,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def _fold(s: str) -> str:
    """Lower + strip diacritics for case/accent-insensitive matching."""
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)
    ).strip()


async def find_contact(
    config: Config,
    user_id: str,
    query: str,
    *,
    limit: int = 5,
) -> list[ContactRecord]:
    """Fuzzy search by name, alias, or handle value (phone/email/instagram).

    Returns top matches sorted by score. Empty list if nothing crosses
    a minimum-confidence threshold — caller should ASK the user rather
    than guess.
    """
    if not query or not query.strip():
        return []

    q = _fold(query)
    contacts = await list_contacts(config, user_id)

    scored: list[tuple[int, ContactRecord]] = []
    for c in contacts:
        name = _fold(c.name_canonical)
        score = 0
        if name == q:
            score = 100
        elif name.startswith(q):
            score = 80
        elif q in name:
            score = 60

        for alias in c.aliases:
            a = _fold(alias)
            if a == q:
                score = max(score, 95)
            elif q in a or a in q:
                score = max(score, 55)

        # Handle values — phone digits, email, instagram handles
        for h_key, h_val in c.handles.items():
            values = h_val if isinstance(h_val, list) else [h_val]
            for v in values:
                if not isinstance(v, str):
                    continue
                vf = _fold(v)
                if vf == q:
                    score = max(score, 90)
                elif q in vf:
                    score = max(score, 50)

        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: (-x[0], x[1].name_canonical))
    return [c for _, c in scored[:limit]]


# ---------------------------------------------------------------------------
# macOS Contacts upsert
# ---------------------------------------------------------------------------


async def upsert_from_macos(
    config: Config,
    user_id: str,
    macos_contact: dict,
) -> tuple[ContactRecord, str]:
    """Insert or update a contact sourced from macOS Contacts.app.

    Manual overrides ALWAYS win — a re-sync never clobbers a name/alias/handle
    the user explicitly set via update_contact(..., mark_manual=True).

    Returns ``(record, action)`` where action is ``created`` | ``updated`` | ``unchanged``.
    """
    external_id = macos_contact.get("id")
    if not external_id:
        raise ValueError("macOS contact missing 'id' field")

    name = (macos_contact.get("name") or "").strip()
    if not name:
        return None, "skipped"  # type: ignore[return-value]

    phones = [p for p in macos_contact.get("phones", []) if p]
    emails = [e for e in macos_contact.get("emails", []) if e]
    instagram = macos_contact.get("instagram") or None

    handles_from_macos: dict[str, Any] = {}
    if phones:
        handles_from_macos["phone"] = phones
    if emails:
        handles_from_macos["email"] = emails
    if instagram:
        handles_from_macos["instagram"] = instagram

    # Look up existing row by (user_id, source, external_id)
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {SELECT_COLS} FROM contacts "
            "WHERE user_id = ? AND source = 'macos_contacts' AND external_id = ?",
            (user_id, external_id),
        )
        row = await cursor.fetchone()

    if row is None:
        record = await create_contact(
            config,
            user_id,
            name=name,
            handles=handles_from_macos,
            source="macos_contacts",
            external_id=external_id,
        )
        return record, "created"

    existing = _row_to_record(row, key)
    overrides = existing.manual_overrides or {}

    # Merge: macOS values are the base, manual overrides win.
    merged_name = overrides.get("name") or name
    merged_aliases = overrides.get("aliases") or existing.aliases
    merged_notes = overrides.get("notes") or existing.notes

    merged_handles = dict(handles_from_macos)
    handle_overrides = overrides.get("handles") or {}
    if isinstance(handle_overrides, dict):
        for h_key, h_val in handle_overrides.items():
            merged_handles[h_key] = h_val

    # Cheap "did anything change" check before writing.
    if (
        merged_name == existing.name_canonical
        and merged_aliases == existing.aliases
        and merged_handles == existing.handles
        and merged_notes == existing.notes
    ):
        return existing, "unchanged"

    now = datetime.now(timezone.utc).isoformat()
    async with db_session(config) as db:
        await db.execute(
            "UPDATE contacts SET "
            "name_canonical = ?, aliases = ?, handles = ?, notes = ?, updated_at = ? "
            "WHERE id = ?",
            (
                encrypt(merged_name, key),
                _enc_json(merged_aliases, key),
                _enc_json(merged_handles, key),
                _enc(merged_notes, key),
                now,
                existing.id,
            ),
        )
        await db.commit()

    return ContactRecord(
        id=existing.id,
        user_id=user_id,
        name_canonical=merged_name,
        aliases=merged_aliases,
        handles=merged_handles,
        notes=merged_notes,
        manual_overrides=overrides,
        source="macos_contacts",
        external_id=external_id,
        created_at=existing.created_at,
        updated_at=now,
    ), "updated"
