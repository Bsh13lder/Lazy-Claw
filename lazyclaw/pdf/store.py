"""Encrypted PDF store — one AES-256-GCM blob per PDF.

Mirrors :mod:`lazyclaw.sheets.store`. ``pdf_files.payload`` is
``encrypt_field(base64(pdf_bytes))`` with AAD ``user_aad(user_id, "pdf:payload")``.
Base64 makes the binary PDF survive a TEXT column; the AAD binds the ciphertext
to its owner + field so values can't be swapped between users or columns.

Plaintext columns (needed for queries / the sidebar): ``id``, ``name``,
``pages``, ``tags``, timestamps. All queries are scoped by ``user_id`` — no
cross-user access.
"""

from __future__ import annotations

import base64
import binascii
import json
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
_TAGS_MAX = 32
_TAG_LEN_MAX = 40

# How many pre-edit snapshots to keep per PDF. In-editor ✨ edits replace the
# live file in place and archive the prior bytes as a hidden ``version_of`` row;
# older snapshots past this cap are pruned so history can't grow unbounded.
MAX_PDF_VERSIONS = 10


def _pdf_aad(user_id: str) -> bytes:
    return user_aad(user_id, "pdf:payload")


def _now() -> str:
    # Microsecond-precision ISO-8601 UTC (mirrors tasks store). The offline-sync
    # /changes feed compares ``updated_at > since`` with a STRICT ``>``; second
    # granularity would silently drop a change made in the same second as the
    # last pull. Microseconds make the cursor reliable.
    return datetime.now(timezone.utc).isoformat()


def _clean_name(name: str | None) -> str:
    base = (name or "").strip() or "document.pdf"
    return base[:_NAME_MAX]


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


def _encode(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


def _decode(b64: str) -> bytes:
    try:
        return base64.b64decode(b64.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Corrupt PDF payload (base64): {exc}") from exc


async def list_pdfs(config: Config, user_id: str) -> list[dict[str, Any]]:
    """Plaintext index: id, name, pages, tags, timestamps (no payload bytes).

    Soft-deleted rows (``deleted_at`` not NULL) are filtered out — they only
    surface through :func:`get_pdf_changes` so offline clients learn of deletes.
    Hidden pre-edit snapshots (``version_of`` not NULL) are also excluded so the
    sidebar shows only live files; they surface via :func:`list_pdf_versions`.
    """
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, pages, tags, created_at, updated_at FROM pdf_files "
            "WHERE user_id = ? AND deleted_at IS NULL AND version_of IS NULL "
            "ORDER BY updated_at DESC",
            (user_id,),
        )
        data = await rows.fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "pages": r[2],
            "tags": _parse_tags(r[3]),
            "created_at": r[4],
            "updated_at": r[5],
        }
        for r in data
    ]


async def get_pdf(
    config: Config, user_id: str, pdf_id: str
) -> dict[str, Any] | None:
    """Fetch + decrypt one PDF. ``bytes`` is the raw decoded PDF, or ``None``.

    Soft-deleted rows return ``None`` (same surface as a missing PDF).
    """
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, payload, pages, tags, created_at, updated_at, version_of "
            "FROM pdf_files WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
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
        "tags": _parse_tags(row[4]),
        "created_at": row[5],
        "updated_at": row[6],
        "version_of": row[7],
    }


async def save_pdf(
    config: Config,
    user_id: str,
    name: str,
    data: bytes,
    pdf_id: str | None = None,
) -> dict[str, Any]:
    """Upsert a PDF. Computes ``pages`` via :func:`ops.page_count`.

    Returns the index row (id, name, pages, tags, timestamps) — never the bytes.
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
                "INSERT INTO pdf_files (id, user_id, name, payload, pages, tags, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pdf_id, user_id, name, enc, pages, "[]", now, now),
            )
            await db.commit()
        return {
            "id": pdf_id,
            "name": name,
            "pages": pages,
            "tags": [],
            "created_at": now,
            "updated_at": now,
        }

    # UPDATE path — read the existing LIVE row first. The ``deleted_at IS NULL``
    # guard makes the save tombstone-aware: a save targeting a soft-deleted id
    # is treated as not-found (same surface as list/get), never mutating the
    # hidden row. We do NOT auto-undelete.
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT tags, created_at FROM pdf_files "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (pdf_id, user_id),
        )
        existing = await cur.fetchone()

    if existing is None:
        # The user-scoped (live-only) SELECT returned nothing.  Before
        # inserting, check whether this id already exists at all — for a
        # different user OR as our own tombstone — and surface the same
        # "not found" surface as a missing PDF to avoid leaking the existence
        # of foreign/tombstoned rows (and to prevent a PK IntegrityError /
        # zombie-row resurrection).
        async with db_session(config) as db:
            probe = await db.execute(
                "SELECT 1 FROM pdf_files WHERE id = ?", (pdf_id,)
            )
            existing_any = await probe.fetchone()
        if existing_any is not None:
            raise LookupError("pdf not found")

        # Unknown but free id (e.g. client-minted) → create.
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO pdf_files (id, user_id, name, payload, pages, tags, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pdf_id, user_id, name, enc, pages, "[]", now, now),
            )
            await db.commit()
        return {
            "id": pdf_id,
            "name": name,
            "pages": pages,
            "tags": [],
            "created_at": now,
            "updated_at": now,
        }

    stored_tags_raw, stored_created_at = existing
    async with db_session(config) as db:
        await db.execute(
            "UPDATE pdf_files SET name = ?, payload = ?, pages = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (name, enc, pages, now, pdf_id, user_id),
        )
        await db.commit()

    # Preserve any existing tags (save_pdf doesn't change tags).
    existing_tags = _parse_tags(stored_tags_raw)

    return {
        "id": pdf_id,
        "name": name,
        "pages": pages,
        "tags": existing_tags,
        "created_at": stored_created_at,
        "updated_at": now,
    }


async def _get_meta_row_any_state(
    config: Config, user_id: str, pdf_id: str
) -> dict[str, Any] | None:
    """Metadata row (no bytes) for ``pdf_id`` regardless of tombstone state.

    Used by the idempotent client-id create path so a replay returns the
    existing row even if it was previously soft-deleted (the row is still there).
    """
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id, name, pages, tags, created_at, updated_at FROM pdf_files "
            "WHERE id = ? AND user_id = ?",
            (pdf_id, user_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "pages": row[2],
        "tags": _parse_tags(row[3]),
        "created_at": row[4],
        "updated_at": row[5],
    }


async def create_pdf(
    config: Config,
    user_id: str,
    name: str,
    data: bytes,
    pdf_id: str | None = None,
) -> dict[str, Any]:
    """Create a new encrypted PDF (the import path) and return its metadata.

    ``pdf_id`` may be a client-minted UUID for offline-first idempotent replay.
    When provided and a row with that id already exists for this user (including
    a soft-deleted one), the existing metadata is returned unchanged — a second
    import with the same id never duplicates the file. Otherwise delegates to
    :func:`save_pdf` which mints (or inserts) the row.
    """
    if pdf_id is not None:
        existing = await _get_meta_row_any_state(config, user_id, pdf_id)
        if existing is not None:
            return existing
    return await save_pdf(config, user_id, name, data, pdf_id=pdf_id)


async def update_pdf_meta(
    config: Config,
    user_id: str,
    pdf_id: str,
    *,
    name: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    """Update name and/or tags for a PDF without touching its payload.

    Returns the refreshed index row (id, name, pages, tags, timestamps) or
    ``None`` when the PDF is not found.
    """
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT name, tags, pages, created_at FROM pdf_files "
            "WHERE id = ? AND user_id = ?",
            (pdf_id, user_id),
        )
        existing = await cur.fetchone()

    if existing is None:
        return None

    stored_name, stored_tags_raw, pages, created_at = existing
    effective_name = _clean_name(name) if name is not None else stored_name
    effective_tags_raw = (
        json.dumps(_clean_tags(tags)) if tags is not None else (stored_tags_raw or "[]")
    )
    now = _now()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE pdf_files SET name = ?, tags = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (effective_name, effective_tags_raw, now, pdf_id, user_id),
        )
        await db.commit()

    return {
        "id": pdf_id,
        "name": effective_name,
        "pages": pages,
        "tags": _parse_tags(effective_tags_raw),
        "created_at": created_at,
        "updated_at": now,
    }


async def delete_pdf(config: Config, user_id: str, pdf_id: str) -> bool:
    """Soft-delete a PDF: set ``deleted_at`` + bump ``updated_at``; keep the row.

    The row is preserved so the offline-sync /changes delta feed can tell
    clients the PDF was deleted. ``list_pdfs``/``get_pdf`` filter
    ``deleted_at IS NULL``. Returns True when a live row was found and
    tombstoned; False when it didn't exist or was already deleted (idempotent).
    """
    now = _now()
    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE pdf_files SET deleted_at = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (now, now, pdf_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def _insert_version_row(
    config: Config, user_id: str, parent_id: str, name: str, data: bytes
) -> str:
    """Insert a hidden pre-edit snapshot row (``version_of = parent_id``).

    Holds the encrypted bytes exactly like a normal PDF, but is filtered out of
    :func:`list_pdfs` / :func:`get_pdf_changes`. Returns the new version id.
    """
    dek = await get_user_dek(config, user_id)
    enc = encrypt_field(_encode(data), dek, _pdf_aad(user_id))
    try:
        pages = ops.page_count(data)
    except ops.PdfError:
        pages = None
    version_id = str(uuid4())
    now = _now()
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO pdf_files (id, user_id, name, payload, pages, tags, "
            "created_at, updated_at, version_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (version_id, user_id, _clean_name(name), enc, pages, "[]", now, now, parent_id),
        )
        await db.commit()
    return version_id


async def _prune_versions(config: Config, user_id: str, parent_id: str) -> None:
    """Keep only the newest :data:`MAX_PDF_VERSIONS` snapshots for ``parent_id``.

    Snapshots are internal recovery rows (never synced), so pruning hard-deletes
    the oldest overflow instead of tombstoning.
    """
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id FROM pdf_files WHERE user_id = ? AND version_of = ? "
            "ORDER BY created_at DESC, id DESC",
            (user_id, parent_id),
        )
        ids = [r[0] for r in await cur.fetchall()]
        stale = ids[MAX_PDF_VERSIONS:]
        for sid in stale:
            await db.execute(
                "DELETE FROM pdf_files WHERE id = ? AND user_id = ?",
                (sid, user_id),
            )
        if stale:
            await db.commit()


async def archive_and_replace(
    config: Config,
    user_id: str,
    pdf_id: str,
    data: bytes,
    *,
    name: str | None = None,
) -> dict[str, Any]:
    """Replace a live PDF's bytes in place, stashing the prior bytes as a version.

    The in-editor ✨ specialist calls this so an edit updates the SAME file (same
    id, same name — the viewer just reloads) instead of forking a suffix-renamed
    duplicate. The pre-edit bytes are archived as a hidden ``version_of`` row so
    the original stays recoverable via :func:`list_pdf_versions` /
    :func:`restore_pdf_version`. History is capped at :data:`MAX_PDF_VERSIONS`.

    Raises ``LookupError`` when ``pdf_id`` is not a live PDF for this user.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("Cannot save an empty PDF.")
    current = await get_pdf(config, user_id, pdf_id)
    if current is None:
        raise LookupError("pdf not found")

    # 1) Snapshot the current (pre-edit) bytes into a hidden version row.
    await _insert_version_row(config, user_id, pdf_id, current["name"], current["bytes"])
    # 2) Overwrite the live row in place (same id; keep name unless renamed).
    updated = await save_pdf(
        config, user_id, name or current["name"], bytes(data), pdf_id=pdf_id
    )
    # 3) Cap retained history.
    await _prune_versions(config, user_id, pdf_id)
    return updated


async def list_pdf_versions(
    config: Config, user_id: str, pdf_id: str
) -> list[dict[str, Any]]:
    """List a PDF's hidden pre-edit snapshots, newest first (metadata, no bytes).

    Each entry's ``id`` fetches bytes via the normal raw route and restores via
    :func:`restore_pdf_version`. Scoped by ``user_id``.
    """
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id, name, pages, created_at, updated_at FROM pdf_files "
            "WHERE user_id = ? AND version_of = ? AND deleted_at IS NULL "
            "ORDER BY created_at DESC, id DESC",
            (user_id, pdf_id),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "pages": r[2],
            "created_at": r[3],
            "updated_at": r[4],
        }
        for r in rows
    ]


async def restore_pdf_version(
    config: Config,
    user_id: str,
    version_id: str,
    *,
    expected_parent: str | None = None,
) -> dict[str, Any] | None:
    """Restore an archived snapshot back into its live parent (undoable).

    Loads the version bytes and replaces the parent's live bytes in place via
    :func:`archive_and_replace` — so the just-replaced bytes are themselves
    archived and restore can be undone. Returns the refreshed parent metadata,
    or ``None`` when the version id is unknown/foreign/not-a-version, its parent
    is gone, or ``expected_parent`` is given and doesn't match — checked BEFORE
    any mutation so a mismatched request never touches another file.
    """
    version = await get_pdf(config, user_id, version_id)
    if version is None or not version.get("version_of"):
        return None
    parent_id = version["version_of"]
    if expected_parent is not None and parent_id != expected_parent:
        return None
    if await get_pdf(config, user_id, parent_id) is None:
        return None
    return await archive_and_replace(
        config, user_id, parent_id, version["bytes"], name=None
    )


async def get_pdf_changes(
    config: Config, user_id: str, since: str | None
) -> dict[str, Any]:
    """Delta feed for offline-first clients (mirrors tasks ``get_task_changes``).

    Items are PDF METADATA only (id, name, pages, tags, timestamps) — never the
    giant base64 blob; the mobile client fetches bytes lazily by id via the raw
    route. Returns rows where ``updated_at > since`` (tombstones included so
    clients learn of deletes). When ``since`` is None/empty, all rows are returned.

    Response shape::

        {
            "files":   [<live metadata rows, no bytes>],
            "deleted": [<id, ...>],   # ids of soft-deleted rows
            "now":     "<server timestamp>",  # use as next `since`
        }
    """
    now_iso = _now()
    # Hidden pre-edit snapshots (``version_of`` not NULL) are a web-only recovery
    # surface — they never sync to offline clients, so the feed excludes them.
    async with db_session(config) as db:
        if since:
            cur = await db.execute(
                "SELECT id, name, pages, tags, created_at, updated_at, deleted_at "
                "FROM pdf_files WHERE user_id = ? AND updated_at > ? "
                "AND version_of IS NULL ORDER BY updated_at ASC",
                (user_id, since),
            )
        else:
            cur = await db.execute(
                "SELECT id, name, pages, tags, created_at, updated_at, deleted_at "
                "FROM pdf_files WHERE user_id = ? AND version_of IS NULL "
                "ORDER BY updated_at ASC",
                (user_id,),
            )
        rows = await cur.fetchall()

    live: list[dict[str, Any]] = []
    deleted: list[str] = []
    for r in rows:
        if r[6] is not None:
            deleted.append(r[0])
        else:
            live.append(
                {
                    "id": r[0],
                    "name": r[1],
                    "pages": r[2],
                    "tags": _parse_tags(r[3]),
                    "created_at": r[4],
                    "updated_at": r[5],
                }
            )
    return {"files": live, "deleted": deleted, "now": now_iso}
