"""Encrypted spreadsheet store — one AES-256-GCM JSON blob per sheet.

Mirrors :mod:`lazyclaw.lazybrain.canvas`: ``sheets.payload`` is ciphertext over
the Univer ``IWorkbookData`` snapshot. Persistence granularity is one blob per
sheet — atomic restore, full UI fidelity, no per-cell schema. All queries are
scoped by ``user_id`` (no cross-user access); the plaintext ``name`` and ``tags``
are used to list sheets in the sidebar.
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
_TAGS_MAX = 32
_TAG_LEN_MAX = 40


class SheetConflictError(Exception):
    """Raised when base_updated_at doesn't match the stored row (CAS failure)."""

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("sheet was modified by another client")
        self.current = current


def _sheets_aad(user_id: str) -> bytes:
    return user_aad(user_id, "sheets:payload")


def _now() -> str:
    # Microsecond-precision ISO-8601 UTC (mirrors tasks store). The offline-sync
    # /changes feed compares ``updated_at > since`` with a STRICT ``>``; second
    # granularity would silently drop a change made in the same second as the
    # last pull. Microseconds make the cursor reliable. CAS only does string
    # equality on this value, so the format change is transparent there.
    return datetime.now(timezone.utc).isoformat()


def _clean_name(name: str | None) -> str:
    return ((name or "").strip() or "Untitled sheet")[:_NAME_MAX]


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


async def list_sheets(config: Config, user_id: str) -> list[dict[str, Any]]:
    """Plaintext index: id, name, tags, timestamps (no payload).

    Soft-deleted rows (``deleted_at`` not NULL) are filtered out — they only
    surface through :func:`get_sheet_changes` so offline clients learn of deletes.
    """
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, tags, created_at, updated_at FROM sheets "
            "WHERE user_id = ? AND deleted_at IS NULL ORDER BY updated_at DESC",
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


async def get_sheet(
    config: Config, user_id: str, sheet_id: str
) -> dict[str, Any] | None:
    """Fetch + decrypt one sheet (payload is the Univer snapshot dict).

    Soft-deleted rows return ``None`` (same surface as a missing sheet).
    """
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, payload, tags, created_at, updated_at "
            "FROM sheets WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
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
        "tags": _parse_tags(row[3]),
        "created_at": row[4],
        "updated_at": row[5],
    }


async def save_sheet(
    config: Config,
    user_id: str,
    name: str | None,
    payload: dict[str, Any],
    sheet_id: str | None = None,
    *,
    base_updated_at: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Upsert a sheet. Returns the index row (no payload).

    When ``base_updated_at`` is provided and does not match the stored
    ``updated_at``, :exc:`SheetConflictError` is raised with ``.current``
    carrying the fresh decrypted row so the caller can show a merge UI.

    When ``name`` is ``None`` the stored name is preserved (rename not intended).
    When ``tags`` is ``None`` the stored tags are preserved.
    """
    dek = await get_user_dek(config, user_id)
    enc = encrypt_field(json.dumps(payload), dek, _sheets_aad(user_id))
    now = _now()

    if sheet_id is None:
        # INSERT new — name required for a blank sheet
        effective_name = _clean_name(name)
        effective_tags = json.dumps(_clean_tags(tags)) if tags is not None else "[]"
        sheet_id = str(uuid4())
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO sheets (id, user_id, name, payload, tags, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sheet_id, user_id, effective_name, enc, effective_tags, now, now),
            )
            await db.commit()
        return {
            "id": sheet_id,
            "name": effective_name,
            "tags": _parse_tags(effective_tags),
            "created_at": now,
            "updated_at": now,
        }

    # UPDATE path — read the existing row first for: name preservation,
    # tags preservation, and conflict detection. The ``deleted_at IS NULL``
    # guard makes the save tombstone-aware: a save targeting a soft-deleted
    # id is treated as not-found (same surface as list/get), never mutating
    # the hidden row. We do NOT auto-undelete.
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT name, tags, updated_at, created_at FROM sheets "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (sheet_id, user_id),
        )
        existing = await cur.fetchone()

    if existing is None:
        # The user-scoped (live-only) SELECT returned nothing.  Before
        # inserting, check whether this id already exists at all — for a
        # different user OR as our own tombstone — and surface the same
        # "not found" surface as a missing sheet to avoid leaking the
        # existence of foreign/tombstoned rows (and to prevent a PK
        # IntegrityError / zombie-row resurrection).
        async with db_session(config) as db:
            probe = await db.execute(
                "SELECT 1 FROM sheets WHERE id = ?", (sheet_id,)
            )
            existing_any = await probe.fetchone()
        if existing_any is not None:
            raise LookupError("sheet not found")

        # Unknown id — create with whatever we were given
        effective_name = _clean_name(name)
        effective_tags = json.dumps(_clean_tags(tags)) if tags is not None else "[]"
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO sheets (id, user_id, name, payload, tags, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sheet_id, user_id, effective_name, enc, effective_tags, now, now),
            )
            await db.commit()
        return {
            "id": sheet_id,
            "name": effective_name,
            "tags": _parse_tags(effective_tags),
            "created_at": now,
            "updated_at": now,
        }

    stored_name, stored_tags_raw, stored_updated_at, stored_created_at = existing

    # Conflict detection (CAS)
    if base_updated_at is not None and base_updated_at != stored_updated_at:
        current = await get_sheet(config, user_id, sheet_id)
        raise SheetConflictError(current)  # type: ignore[arg-type]

    effective_name = _clean_name(name) if name is not None else stored_name
    effective_tags_raw = (
        json.dumps(_clean_tags(tags)) if tags is not None else (stored_tags_raw or "[]")
    )

    async with db_session(config) as db:
        await db.execute(
            "UPDATE sheets SET name = ?, payload = ?, tags = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (effective_name, enc, effective_tags_raw, now, sheet_id, user_id),
        )
        await db.commit()

    return {
        "id": sheet_id,
        "name": effective_name,
        "tags": _parse_tags(effective_tags_raw),
        "created_at": stored_created_at,
        "updated_at": now,
    }


async def create_sheet(
    config: Config, user_id: str, name: str, sheet_id: str | None = None
) -> dict[str, Any]:
    """Create a new blank sheet and return its index row.

    ``sheet_id`` may be a client-minted UUID for offline-first idempotent
    replay. When provided and a row with that id already exists for this user
    (including a soft-deleted one), the existing row is returned unchanged —
    a second POST with the same id never duplicates the sheet.
    """
    name = _clean_name(name)
    if sheet_id is not None:
        existing = await _get_index_row_any_state(config, user_id, sheet_id)
        if existing is not None:
            return existing
    return await save_sheet(config, user_id, name, blank_workbook(name), sheet_id=sheet_id)


async def _get_index_row_any_state(
    config: Config, user_id: str, sheet_id: str
) -> dict[str, Any] | None:
    """Index row (no payload) for ``sheet_id`` regardless of tombstone state.

    Used by the idempotent client-id create path so a replay returns the live
    row even if it was previously soft-deleted (the row is still there).
    """
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id, name, tags, created_at, updated_at FROM sheets "
            "WHERE id = ? AND user_id = ?",
            (sheet_id, user_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "tags": _parse_tags(row[2]),
        "created_at": row[3],
        "updated_at": row[4],
    }


async def delete_sheet(config: Config, user_id: str, sheet_id: str) -> bool:
    """Soft-delete a sheet: set ``deleted_at`` + bump ``updated_at``; keep the row.

    The row is preserved so the offline-sync /changes delta feed can tell
    clients the sheet was deleted. ``list_sheets``/``get_sheet`` filter
    ``deleted_at IS NULL``. Returns True when a live row was found and
    tombstoned; False when it didn't exist or was already deleted (idempotent).
    """
    now = _now()
    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE sheets SET deleted_at = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (now, now, sheet_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_sheet_changes(
    config: Config, user_id: str, since: str | None
) -> dict[str, Any]:
    """Delta feed for offline-first clients (mirrors tasks ``get_task_changes``).

    Returns rows where ``updated_at > since`` (tombstones included so clients
    learn of deletes). When ``since`` is None/empty, all rows are returned.

    Response shape::

        {
            "sheets":  [<live index rows, no payload>],
            "deleted": [<id, ...>],   # ids of soft-deleted rows
            "now":     "<server timestamp>",  # use as next `since`
        }
    """
    now_iso = _now()
    async with db_session(config) as db:
        if since:
            cur = await db.execute(
                "SELECT id, name, tags, created_at, updated_at, deleted_at "
                "FROM sheets WHERE user_id = ? AND updated_at > ? "
                "ORDER BY updated_at ASC",
                (user_id, since),
            )
        else:
            cur = await db.execute(
                "SELECT id, name, tags, created_at, updated_at, deleted_at "
                "FROM sheets WHERE user_id = ? ORDER BY updated_at ASC",
                (user_id,),
            )
        rows = await cur.fetchall()

    live: list[dict[str, Any]] = []
    deleted: list[str] = []
    for r in rows:
        if r[5] is not None:
            deleted.append(r[0])
        else:
            live.append(
                {
                    "id": r[0],
                    "name": r[1],
                    "tags": _parse_tags(r[2]),
                    "created_at": r[3],
                    "updated_at": r[4],
                }
            )
    return {"sheets": live, "deleted": deleted, "now": now_iso}
