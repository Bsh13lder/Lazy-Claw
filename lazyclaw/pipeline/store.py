"""Pipeline storage: encrypted CRUD for contacts and deals.

Generic CRM/pipeline system. Stages are user-defined strings —
no hardcoded business logic. Users create custom skills on top.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENCRYPTED_CONTACT_FIELDS = frozenset({"name", "phone", "email", "notes"})
ENCRYPTED_DEAL_FIELDS = frozenset({"title", "description", "data"})

CONTACT_COLUMNS = [
    "id", "user_id", "name", "phone", "email", "notes",
    "stage", "tags", "created_at", "updated_at",
]

DEAL_COLUMNS = [
    "id", "user_id", "contact_id", "title", "description",
    "amount", "currency", "stage", "data", "created_at", "updated_at",
]

CONTACT_SELECT = ", ".join(CONTACT_COLUMNS)
DEAL_SELECT = ", ".join(DEAL_COLUMNS)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enc(value: str | None, key: bytes) -> str | None:
    if value is None:
        return None
    return encrypt(value, key)


def _contact_row_to_dict(row, key: bytes) -> dict:
    result = {}
    for i, col in enumerate(CONTACT_COLUMNS):
        value = row[i]
        if col in ENCRYPTED_CONTACT_FIELDS:
            value = decrypt_field(value, key)
        result[col] = value
    return result


def _deal_row_to_dict(row, key: bytes) -> dict:
    result = {}
    for i, col in enumerate(DEAL_COLUMNS):
        value = row[i]
        if col in ENCRYPTED_DEAL_FIELDS:
            value = decrypt_field(value, key)
        result[col] = value
    return result


# ---------------------------------------------------------------------------
# Contacts CRUD
# ---------------------------------------------------------------------------


async def create_contact(
    config: Config,
    user_id: str,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
    stage: str = "new",
    tags: str | None = None,
) -> dict:
    """Create a new contact. Returns decrypted dict."""
    key = await get_user_dek(config, user_id)
    contact_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO pipeline_contacts "
            "(id, user_id, name, phone, email, notes, stage, tags, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                contact_id, user_id,
                encrypt(name, key), _enc(phone, key), _enc(email, key),
                _enc(notes, key), stage, tags, now, now,
            ),
        )
        await db.commit()

    logger.debug("Created contact %s for user %s", contact_id, user_id)
    return {
        "id": contact_id, "user_id": user_id, "name": name,
        "phone": phone, "email": email, "notes": notes,
        "stage": stage, "tags": tags,
        "created_at": now, "updated_at": now,
    }


async def list_contacts(
    config: Config,
    user_id: str,
    stage: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """List contacts, optionally filtered by stage. Search decrypts and matches."""
    key = await get_user_dek(config, user_id)

    where = "user_id = ?"
    params: list = [user_id]
    if stage:
        where += " AND stage = ?"
        params.append(stage)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {CONTACT_SELECT} FROM pipeline_contacts "
            f"WHERE {where} ORDER BY updated_at DESC",
            params,
        )
        rows = await cursor.fetchall()

    contacts = [_contact_row_to_dict(r, key) for r in rows]

    if search:
        q = search.lower()
        contacts = [
            c for c in contacts
            if q in (c.get("name") or "").lower()
            or q in (c.get("phone") or "").lower()
            or q in (c.get("email") or "").lower()
            or q in (c.get("notes") or "").lower()
        ]

    return contacts


async def get_contact(config: Config, user_id: str, contact_id: str) -> dict | None:
    """Get a single contact by ID."""
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {CONTACT_SELECT} FROM pipeline_contacts "
            "WHERE id = ? AND user_id = ?",
            (contact_id, user_id),
        )
        row = await cursor.fetchone()

    if not row:
        return None
    return _contact_row_to_dict(row, key)


async def update_contact(
    config: Config, user_id: str, contact_id: str, **fields
) -> bool:
    """Update contact fields. Returns True if updated."""
    if not fields:
        return False

    key = await get_user_dek(config, user_id)
    set_clauses: list[str] = []
    params: list = []

    for col, value in fields.items():
        if col in ENCRYPTED_CONTACT_FIELDS and value is not None:
            value = encrypt(value, key)
        set_clauses.append(f"{col} = ?")
        params.append(value)

    set_clauses.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.extend([contact_id, user_id])

    async with db_session(config) as db:
        result = await db.execute(
            f"UPDATE pipeline_contacts SET {', '.join(set_clauses)} "
            "WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()
        return result.rowcount > 0


async def delete_contact(config: Config, user_id: str, contact_id: str) -> bool:
    """Delete contact and associated deals."""
    async with db_session(config) as db:
        await db.execute(
            "DELETE FROM pipeline_deals WHERE contact_id = ? AND user_id = ?",
            (contact_id, user_id),
        )
        result = await db.execute(
            "DELETE FROM pipeline_contacts WHERE id = ? AND user_id = ?",
            (contact_id, user_id),
        )
        await db.commit()
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Deals CRUD
# ---------------------------------------------------------------------------


async def create_deal(
    config: Config,
    user_id: str,
    contact_id: str,
    title: str,
    description: str | None = None,
    amount: float = 0,
    currency: str = "EUR",
    stage: str = "inquiry",
    data: str | None = None,
) -> dict:
    """Create a deal linked to a contact. Returns decrypted dict."""
    key = await get_user_dek(config, user_id)
    deal_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO pipeline_deals "
            "(id, user_id, contact_id, title, description, amount, currency, stage, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                deal_id, user_id, contact_id,
                encrypt(title, key), _enc(description, key),
                amount, currency, stage, _enc(data, key), now, now,
            ),
        )
        await db.commit()

    logger.debug("Created deal %s for contact %s", deal_id, contact_id)
    return {
        "id": deal_id, "user_id": user_id, "contact_id": contact_id,
        "title": title, "description": description,
        "amount": amount, "currency": currency, "stage": stage,
        "data": data, "created_at": now, "updated_at": now,
    }


async def list_deals(
    config: Config,
    user_id: str,
    contact_id: str | None = None,
    stage: str | None = None,
) -> list[dict]:
    """List deals, optionally filtered by contact and/or stage."""
    key = await get_user_dek(config, user_id)

    where = "user_id = ?"
    params: list = [user_id]
    if contact_id:
        where += " AND contact_id = ?"
        params.append(contact_id)
    if stage:
        where += " AND stage = ?"
        params.append(stage)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {DEAL_SELECT} FROM pipeline_deals "
            f"WHERE {where} ORDER BY updated_at DESC",
            params,
        )
        rows = await cursor.fetchall()

    return [_deal_row_to_dict(r, key) for r in rows]


async def update_deal(
    config: Config, user_id: str, deal_id: str, **fields
) -> bool:
    """Update deal fields. Returns True if updated."""
    if not fields:
        return False

    key = await get_user_dek(config, user_id)
    set_clauses: list[str] = []
    params: list = []

    for col, value in fields.items():
        if col in ENCRYPTED_DEAL_FIELDS and value is not None:
            value = encrypt(value, key)
        set_clauses.append(f"{col} = ?")
        params.append(value)

    set_clauses.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.extend([deal_id, user_id])

    async with db_session(config) as db:
        result = await db.execute(
            f"UPDATE pipeline_deals SET {', '.join(set_clauses)} "
            "WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()
        return result.rowcount > 0


async def delete_deal(config: Config, user_id: str, deal_id: str) -> bool:
    """Delete a deal."""
    async with db_session(config) as db:
        result = await db.execute(
            "DELETE FROM pipeline_deals WHERE id = ? AND user_id = ?",
            (deal_id, user_id),
        )
        await db.commit()
        return result.rowcount > 0
