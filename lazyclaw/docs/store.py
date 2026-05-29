"""Encrypted document store — one AES-256-GCM JSON blob per doc.

Mirrors :mod:`lazyclaw.sheets.store`: ``docs.payload`` is ciphertext over the
Univer ``IDocumentData`` snapshot. Persistence granularity is one blob per
doc — atomic restore, full UI fidelity, no per-paragraph schema. All queries
are scoped by ``user_id`` (no cross-user access); the plaintext ``name`` is
used to list docs in the sidebar.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt_field, user_aad
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.docs.snapshot import blank_document

logger = logging.getLogger(__name__)

_NAME_MAX = 120


def _docs_aad(user_id: str) -> bytes:
    return user_aad(user_id, "docs:payload")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _clean_name(name: str | None) -> str:
    return ((name or "").strip() or "Untitled doc")[:_NAME_MAX]


def _empty_payload(name: str) -> dict[str, Any]:
    return blank_document(name)


async def list_docs(config: Config, user_id: str) -> list[dict[str, Any]]:
    """Plaintext index: id, name, timestamps (no payload)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, created_at, updated_at FROM docs "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        data = await rows.fetchall()
    return [
        {"id": r[0], "name": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in data
    ]


async def get_doc(
    config: Config, user_id: str, doc_id: str
) -> dict[str, Any] | None:
    """Fetch + decrypt one doc (payload is the Univer snapshot dict)."""
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, payload, created_at, updated_at "
            "FROM docs WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        )
        row = await rows.fetchone()
    if not row:
        return None

    raw = decrypt_field(row[2], dek, _docs_aad(user_id), fallback="")
    try:
        payload = json.loads(raw) if raw else _empty_payload(row[1])
    except json.JSONDecodeError:
        logger.warning("doc %s payload failed to parse — returning blank", doc_id)
        payload = _empty_payload(row[1])
    return {
        "id": row[0],
        "name": row[1],
        "payload": payload,
        "created_at": row[3],
        "updated_at": row[4],
    }


async def save_doc(
    config: Config,
    user_id: str,
    name: str,
    payload: dict[str, Any],
    doc_id: str | None = None,
) -> dict[str, Any]:
    """Upsert a doc. Returns the index row (no payload)."""
    dek = await get_user_dek(config, user_id)
    enc = encrypt_field(json.dumps(payload), dek, _docs_aad(user_id))
    now = _now()
    name = _clean_name(name)

    if doc_id is None:
        doc_id = str(uuid4())
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO docs (id, user_id, name, payload, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, user_id, name, enc, now, now),
            )
            await db.commit()
        return {"id": doc_id, "name": name, "created_at": now, "updated_at": now}

    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE docs SET name = ?, payload = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (name, enc, now, doc_id, user_id),
        )
        if cur.rowcount == 0:
            # Caller passed an id that doesn't exist (or isn't theirs) → create.
            await db.execute(
                "INSERT INTO docs (id, user_id, name, payload, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, user_id, name, enc, now, now),
            )
        await db.commit()
    return {"id": doc_id, "name": name, "updated_at": now}


async def create_doc(
    config: Config, user_id: str, name: str
) -> dict[str, Any]:
    """Create a new blank doc and return its index row."""
    name = _clean_name(name)
    return await save_doc(config, user_id, name, blank_document(name))


async def delete_doc(config: Config, user_id: str, doc_id: str) -> bool:
    async with db_session(config) as db:
        cur = await db.execute(
            "DELETE FROM docs WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0
