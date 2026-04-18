"""Encrypted free-form canvas boards for LazyBrain.

A canvas is an Obsidian-Canvas-style spatial workspace: note cards, free-form
text, and arrows. Persistence granularity is one encrypted JSON blob per
board — simpler than row-per-node and easier to restore atomically.

Schema: ``canvas_boards(id, user_id, name, payload, created_at, updated_at)``.
``payload`` is AES-256-GCM ciphertext over the React-Flow-shaped dict
``{nodes: [...], edges: [...], viewport: {...}}``.
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

logger = logging.getLogger(__name__)


def _canvas_aad(user_id: str) -> bytes:
    return user_aad(user_id, "canvas:payload")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _empty_payload() -> dict[str, Any]:
    return {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}


async def list_boards(config: Config, user_id: str) -> list[dict[str, Any]]:
    """Return a plaintext index: id, name, timestamps (no payload)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, created_at, updated_at FROM canvas_boards "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        data = await rows.fetchall()
    return [
        {"id": r[0], "name": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in data
    ]


async def get_board(
    config: Config, user_id: str, board_id: str
) -> dict[str, Any] | None:
    """Fetch + decrypt one board."""
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, payload, created_at, updated_at "
            "FROM canvas_boards WHERE id = ? AND user_id = ?",
            (board_id, user_id),
        )
        row = await rows.fetchone()
    if not row:
        return None

    raw = decrypt_field(row[2], dek, _canvas_aad(user_id), fallback="")
    try:
        payload = json.loads(raw) if raw else _empty_payload()
    except json.JSONDecodeError:
        payload = _empty_payload()
    return {
        "id": row[0],
        "name": row[1],
        "payload": payload,
        "created_at": row[3],
        "updated_at": row[4],
    }


async def save_board(
    config: Config,
    user_id: str,
    name: str,
    payload: dict[str, Any],
    board_id: str | None = None,
) -> dict[str, Any]:
    """Upsert a board. Returns the index row (no payload)."""
    dek = await get_user_dek(config, user_id)
    enc = encrypt_field(json.dumps(payload), dek, _canvas_aad(user_id))
    now = _now()
    name = (name or "").strip() or "Untitled canvas"

    if board_id is None:
        board_id = str(uuid4())
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO canvas_boards (id, user_id, name, payload, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (board_id, user_id, name[:120], enc, now, now),
            )
            await db.commit()
        return {"id": board_id, "name": name, "created_at": now, "updated_at": now}

    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE canvas_boards SET name = ?, payload = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (name[:120], enc, now, board_id, user_id),
        )
        if cur.rowcount == 0:
            # Caller passed a board_id that doesn't exist → create it
            await db.execute(
                "INSERT INTO canvas_boards (id, user_id, name, payload, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (board_id, user_id, name[:120], enc, now, now),
            )
        await db.commit()
    return {"id": board_id, "name": name, "updated_at": now}


async def delete_board(config: Config, user_id: str, board_id: str) -> bool:
    async with db_session(config) as db:
        cur = await db.execute(
            "DELETE FROM canvas_boards WHERE id = ? AND user_id = ?",
            (board_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0
