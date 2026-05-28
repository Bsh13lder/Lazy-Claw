"""Encrypted spreadsheet store — one AES-256-GCM JSON blob per sheet.

Mirrors :mod:`lazyclaw.lazybrain.canvas`: ``sheets.payload`` is ciphertext over
the Univer ``IWorkbookData`` snapshot. Persistence granularity is one blob per
sheet — atomic restore, full UI fidelity, no per-cell schema. All queries are
scoped by ``user_id`` (no cross-user access); the plaintext ``name`` is used to
list sheets in the sidebar.
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
from lazyclaw.sheets.snapshot import blank_workbook

logger = logging.getLogger(__name__)

_NAME_MAX = 120


def _sheets_aad(user_id: str) -> bytes:
    return user_aad(user_id, "sheets:payload")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _clean_name(name: str | None) -> str:
    return ((name or "").strip() or "Untitled sheet")[:_NAME_MAX]


async def list_sheets(config: Config, user_id: str) -> list[dict[str, Any]]:
    """Plaintext index: id, name, timestamps (no payload)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, created_at, updated_at FROM sheets "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        data = await rows.fetchall()
    return [
        {"id": r[0], "name": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in data
    ]


async def get_sheet(
    config: Config, user_id: str, sheet_id: str
) -> dict[str, Any] | None:
    """Fetch + decrypt one sheet (payload is the Univer snapshot dict)."""
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, payload, created_at, updated_at "
            "FROM sheets WHERE id = ? AND user_id = ?",
            (sheet_id, user_id),
        )
        row = await rows.fetchone()
    if not row:
        return None

    raw = decrypt_field(row[2], dek, _sheets_aad(user_id), fallback="")
    try:
        payload = json.loads(raw) if raw else blank_workbook(row[1])
    except json.JSONDecodeError:
        logger.warning("sheet %s payload failed to parse — returning blank", sheet_id)
        payload = blank_workbook(row[1])
    return {
        "id": row[0],
        "name": row[1],
        "payload": payload,
        "created_at": row[3],
        "updated_at": row[4],
    }


async def save_sheet(
    config: Config,
    user_id: str,
    name: str,
    payload: dict[str, Any],
    sheet_id: str | None = None,
) -> dict[str, Any]:
    """Upsert a sheet. Returns the index row (no payload)."""
    dek = await get_user_dek(config, user_id)
    enc = encrypt_field(json.dumps(payload), dek, _sheets_aad(user_id))
    now = _now()
    name = _clean_name(name)

    if sheet_id is None:
        sheet_id = str(uuid4())
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO sheets (id, user_id, name, payload, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sheet_id, user_id, name, enc, now, now),
            )
            await db.commit()
        return {"id": sheet_id, "name": name, "created_at": now, "updated_at": now}

    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE sheets SET name = ?, payload = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (name, enc, now, sheet_id, user_id),
        )
        if cur.rowcount == 0:
            # Caller passed an id that doesn't exist (or isn't theirs) → create.
            await db.execute(
                "INSERT INTO sheets (id, user_id, name, payload, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sheet_id, user_id, name, enc, now, now),
            )
        await db.commit()
    return {"id": sheet_id, "name": name, "updated_at": now}


async def create_sheet(
    config: Config, user_id: str, name: str
) -> dict[str, Any]:
    """Create a new blank sheet and return its index row."""
    name = _clean_name(name)
    return await save_sheet(config, user_id, name, blank_workbook(name))


async def delete_sheet(config: Config, user_id: str, sheet_id: str) -> bool:
    async with db_session(config) as db:
        cur = await db.execute(
            "DELETE FROM sheets WHERE id = ? AND user_id = ?",
            (sheet_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0
