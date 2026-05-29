"""Encrypted PDF store — one AES-256-GCM blob per PDF.

Mirrors :mod:`lazyclaw.sheets.store`. ``pdf_files.payload`` is
``encrypt_field(base64(pdf_bytes))`` with AAD ``user_aad(user_id, "pdf:payload")``.
Base64 makes the binary PDF survive a TEXT column; the AAD binds the ciphertext
to its owner + field so values can't be swapped between users or columns.

Plaintext columns (needed for queries / the sidebar): ``id``, ``name``,
``pages``, timestamps. All queries are scoped by ``user_id`` — no cross-user
access.
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt_field, user_aad
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.pdf import ops

logger = logging.getLogger(__name__)

_NAME_MAX = 160


def _pdf_aad(user_id: str) -> bytes:
    return user_aad(user_id, "pdf:payload")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _clean_name(name: str | None) -> str:
    base = (name or "").strip() or "document.pdf"
    return base[:_NAME_MAX]


def _encode(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


def _decode(b64: str) -> bytes:
    try:
        return base64.b64decode(b64.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Corrupt PDF payload (base64): {exc}") from exc


async def list_pdfs(config: Config, user_id: str) -> list[dict[str, Any]]:
    """Plaintext index: id, name, pages, timestamps (no payload bytes)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, pages, created_at, updated_at FROM pdf_files "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        data = await rows.fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "pages": r[2],
            "created_at": r[3],
            "updated_at": r[4],
        }
        for r in data
    ]


async def get_pdf(
    config: Config, user_id: str, pdf_id: str
) -> dict[str, Any] | None:
    """Fetch + decrypt one PDF. ``bytes`` is the raw decoded PDF, or ``None``."""
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, payload, pages, created_at, updated_at "
            "FROM pdf_files WHERE id = ? AND user_id = ?",
            (pdf_id, user_id),
        )
        row = await rows.fetchone()
    if not row:
        return None

    b64 = decrypt_field(row[2], dek, _pdf_aad(user_id), fallback="")
    if not b64:
        logger.warning("pdf %s payload failed to decrypt", pdf_id)
        raw = b""
    else:
        try:
            raw = _decode(b64)
        except ValueError:
            logger.warning("pdf %s payload failed to base64-decode", pdf_id)
            raw = b""
    return {
        "id": row[0],
        "name": row[1],
        "bytes": raw,
        "pages": row[3],
        "created_at": row[4],
        "updated_at": row[5],
    }


async def save_pdf(
    config: Config,
    user_id: str,
    name: str,
    data: bytes,
    pdf_id: str | None = None,
) -> dict[str, Any]:
    """Upsert a PDF. Computes ``pages`` via :func:`ops.page_count`.

    Returns the index row (id, name, pages, timestamps) — never the bytes.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("Cannot save an empty PDF.")
    dek = await get_user_dek(config, user_id)
    enc = encrypt_field(_encode(data), dek, _pdf_aad(user_id))
    name = _clean_name(name)
    now = _now()

    try:
        pages = ops.page_count(data)
    except ops.PdfError as exc:
        logger.warning("save_pdf: page_count failed for %s: %s", name, exc)
        pages = None

    if pdf_id is None:
        pdf_id = str(uuid4())
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO pdf_files (id, user_id, name, payload, pages, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pdf_id, user_id, name, enc, pages, now, now),
            )
            await db.commit()
        return {
            "id": pdf_id,
            "name": name,
            "pages": pages,
            "created_at": now,
            "updated_at": now,
        }

    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE pdf_files SET name = ?, payload = ?, pages = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (name, enc, pages, now, pdf_id, user_id),
        )
        if cur.rowcount == 0:
            # Caller passed an id that doesn't exist (or isn't theirs) → create.
            await db.execute(
                "INSERT INTO pdf_files (id, user_id, name, payload, pages, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pdf_id, user_id, name, enc, pages, now, now),
            )
        await db.commit()
    return {"id": pdf_id, "name": name, "pages": pages, "updated_at": now}


async def delete_pdf(config: Config, user_id: str, pdf_id: str) -> bool:
    async with db_session(config) as db:
        cur = await db.execute(
            "DELETE FROM pdf_files WHERE id = ? AND user_id = ?",
            (pdf_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0
