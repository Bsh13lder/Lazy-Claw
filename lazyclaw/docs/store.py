"""Encrypted document store — one AES-256-GCM JSON blob per doc.

Mirrors :mod:`lazyclaw.sheets.store`: ``docs.payload`` is ciphertext over the
Univer ``IDocumentData`` snapshot. Persistence granularity is one blob per
doc — atomic restore, full UI fidelity, no per-paragraph schema. All queries
are scoped by ``user_id`` (no cross-user access); the plaintext ``name`` and
``tags`` are used to list docs in the sidebar.
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
_TAGS_MAX = 32
_TAG_LEN_MAX = 40


class DocConflictError(Exception):
    """Raised when base_updated_at doesn't match the stored row (CAS failure)."""

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("doc was modified by another client")
        self.current = current


def _docs_aad(user_id: str) -> bytes:
    return user_aad(user_id, "docs:payload")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _clean_name(name: str | None) -> str:
    return ((name or "").strip() or "Untitled doc")[:_NAME_MAX]


def _empty_payload(name: str) -> dict[str, Any]:
    return blank_document(name)


def _clean_tags(tags: Any) -> list[str]:
    """Sanitise a tags value: must be a list, max 32 tags, each ≤40 chars, deduped."""
    if not isinstance(tags, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        s = str(t).strip()[:_TAG_LEN_MAX]
        if s and s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= _TAGS_MAX:
            break
    return out


def _parse_tags(raw: str | None) -> list[str]:
    """Parse a JSON tags string stored in DB; returns [] on any error."""
    try:
        result = json.loads(raw or "[]")
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def list_docs(config: Config, user_id: str) -> list[dict[str, Any]]:
    """Plaintext index: id, name, tags, timestamps (no payload)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, tags, created_at, updated_at FROM docs "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        data = await rows.fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "tags": _parse_tags(r[2]),
            "created_at": r[3],
            "updated_at": r[4],
        }
        for r in data
    ]


async def get_doc(
    config: Config, user_id: str, doc_id: str
) -> dict[str, Any] | None:
    """Fetch + decrypt one doc (payload is the Univer snapshot dict)."""
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, payload, tags, created_at, updated_at "
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
        "tags": _parse_tags(row[3]),
        "created_at": row[4],
        "updated_at": row[5],
    }


async def save_doc(
    config: Config,
    user_id: str,
    name: str | None,
    payload: dict[str, Any],
    doc_id: str | None = None,
    *,
    base_updated_at: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Upsert a doc. Returns the index row (no payload).

    When ``base_updated_at`` is provided and does not match the stored
    ``updated_at``, :exc:`DocConflictError` is raised with ``.current``
    carrying the fresh decrypted row so the caller can show a merge UI.

    When ``name`` is ``None`` the stored name is preserved (rename not intended).
    When ``tags`` is ``None`` the stored tags are preserved.
    """
    dek = await get_user_dek(config, user_id)
    enc = encrypt_field(json.dumps(payload), dek, _docs_aad(user_id))
    now = _now()

    if doc_id is None:
        effective_name = _clean_name(name)
        effective_tags = json.dumps(_clean_tags(tags)) if tags is not None else "[]"
        doc_id = str(uuid4())
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO docs (id, user_id, name, payload, tags, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (doc_id, user_id, effective_name, enc, effective_tags, now, now),
            )
            await db.commit()
        return {
            "id": doc_id,
            "name": effective_name,
            "tags": _parse_tags(effective_tags),
            "created_at": now,
            "updated_at": now,
        }

    # UPDATE path — read existing row for name/tags preservation + conflict check
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT name, tags, updated_at FROM docs WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        )
        existing = await cur.fetchone()

    if existing is None:
        # The user-scoped SELECT returned nothing.  Before inserting, check
        # whether this id belongs to a different user — if so, surface the
        # same "not found" surface as a missing doc to avoid leaking the
        # existence of foreign rows (and to prevent a PK IntegrityError).
        async with db_session(config) as db:
            probe = await db.execute(
                "SELECT 1 FROM docs WHERE id = ?", (doc_id,)
            )
            foreign = await probe.fetchone()
        if foreign is not None:
            raise LookupError("doc not found")

        effective_name = _clean_name(name)
        effective_tags = json.dumps(_clean_tags(tags)) if tags is not None else "[]"
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO docs (id, user_id, name, payload, tags, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (doc_id, user_id, effective_name, enc, effective_tags, now, now),
            )
            await db.commit()
        return {
            "id": doc_id,
            "name": effective_name,
            "tags": _parse_tags(effective_tags),
            "updated_at": now,
        }

    stored_name, stored_tags_raw, stored_updated_at = existing

    # Conflict detection (CAS)
    if base_updated_at is not None and base_updated_at != stored_updated_at:
        current = await get_doc(config, user_id, doc_id)
        raise DocConflictError(current)  # type: ignore[arg-type]

    effective_name = _clean_name(name) if name is not None else stored_name
    effective_tags_raw = (
        json.dumps(_clean_tags(tags)) if tags is not None else (stored_tags_raw or "[]")
    )

    async with db_session(config) as db:
        await db.execute(
            "UPDATE docs SET name = ?, payload = ?, tags = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (effective_name, enc, effective_tags_raw, now, doc_id, user_id),
        )
        await db.commit()

    return {
        "id": doc_id,
        "name": effective_name,
        "tags": _parse_tags(effective_tags_raw),
        "updated_at": now,
    }


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
