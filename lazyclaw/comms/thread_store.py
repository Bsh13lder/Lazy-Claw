"""Encrypted store for cross-channel inbox threads (metadata only —
message bodies are read live via ChannelGateway)."""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt_field, decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

# Channel names come from MCP watcher service ids and HTTP filters — keep them
# sane lowercase tokens (hyphens allowed: MCP service ids use them).
# Deliberately NOT a closed allowlist: custom MCP watchers may mirror threads
# for services beyond the built-in comms channels.
_CHANNEL_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# Sentinel `decrypt_field` returns when an `enc:`-prefixed value fails to
# decrypt (wrong key / corruption). Must mirror the default `fallback` in
# `crypto.encryption.decrypt_field`. A row whose handle/name decrypts to this
# is unreadable — we flag it so the client renders a distinct "re-sync" state
# instead of a broken thread titled "[encrypted]" whose live read also fails.
_DECRYPT_SENTINEL = "[encrypted]"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _handle_hash(key: bytes, contact_handle: str) -> str:
    """Deterministic HMAC of the contact handle, used for the dedup lookup +
    unique index. Handles are PII (phone numbers, WhatsApp JIDs, emails), so
    the stored column is encrypted and only this keyed hash is queryable."""
    return hmac.new(key, contact_handle.encode("utf-8"), hashlib.sha256).hexdigest()


def _row_to_dict(row, key: bytes) -> dict:
    # Plaintext-tolerant decrypt: rows written before handle encryption
    # (2026-06-10) hold the raw handle and pass through unchanged.
    contact_handle = decrypt_field(row["contact_handle"], key)
    contact_name = decrypt_field(row["contact_name"], key)
    # A row whose identity fields decrypt to the sentinel is mis-keyed /
    # corrupted — surface it so the UI can render a distinct "unreadable —
    # re-sync" state rather than a broken thread whose live read also fails.
    decrypt_error = (
        contact_handle == _DECRYPT_SENTINEL or contact_name == _DECRYPT_SENTINEL
    )
    return {
        "id": row["id"],
        "channel": row["channel"],
        "contact_handle": contact_handle,
        "contact_name": contact_name,
        "last_preview": decrypt_field(row["last_preview"], key),
        "unread_count": row["unread_count"],
        "last_activity": row["last_activity"],
        "last_seen_msg_id": decrypt_field(row["last_seen_msg_id"], key),
        # Per-contact standing instruction (column added 2026-06-10; tolerate
        # rows read before the migration ran).
        "instruction": (
            decrypt_field(row["instruction"], key)
            if "instruction" in row.keys() else None
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
        "decrypt_error": decrypt_error,
    }


async def upsert_thread(
    config: Config,
    user_id: str,
    *,
    channel: str,
    contact_handle: str,
    contact_name: str | None = None,
    preview: str | None = None,
    last_seen_msg_id: str | None = None,
    increment_unread: bool = False,
) -> dict:
    """Create or update a thread record, returning the decrypted dict.

    Deduplication key: (user_id, channel, HMAC(contact_handle)) — the handle
    itself is stored encrypted (it's PII: phone numbers / JIDs / emails).
    When a thread already exists, unread_count is incremented if
    ``increment_unread`` is True; encrypted fields are refreshed.
    A soft-deleted thread is revived (deleted_at=NULL) on upsert.
    """
    if not _CHANNEL_RE.match(channel or ""):
        raise ValueError(f"invalid channel name: {channel!r}")
    key = await get_user_dek(config, user_id)
    handle_hash = _handle_hash(key, contact_handle)
    now = _now()
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id, unread_count FROM channel_threads "
            "WHERE user_id=? AND channel=? AND contact_handle_hash=?",
            (user_id, channel, handle_hash),
        )
        existing = await cur.fetchone()
        if existing is None:
            # Legacy row from before handle encryption (plaintext handle,
            # NULL hash) — claim it so the upsert upgrades it in place
            # instead of creating a duplicate thread.
            cur = await db.execute(
                "SELECT id, unread_count FROM channel_threads "
                "WHERE user_id=? AND channel=? AND contact_handle=? "
                "AND contact_handle_hash IS NULL",
                (user_id, channel, contact_handle),
            )
            existing = await cur.fetchone()
        if existing is None:
            tid = str(uuid4())
            await db.execute(
                "INSERT INTO channel_threads "
                "(id,user_id,channel,contact_handle,contact_handle_hash,"
                "contact_name,last_preview,"
                "unread_count,last_activity,last_seen_msg_id,created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    tid, user_id, channel,
                    encrypt_field(contact_handle, key),
                    handle_hash,
                    encrypt_field(contact_name, key),
                    encrypt_field(preview, key),
                    1 if increment_unread else 0,
                    now,
                    encrypt_field(last_seen_msg_id, key),
                    now, now,
                ),
            )
        else:
            tid = existing["id"]
            new_unread = existing["unread_count"] + (1 if increment_unread else 0)
            await db.execute(
                "UPDATE channel_threads SET "
                "contact_handle=?, contact_handle_hash=?, "
                "contact_name=COALESCE(?, contact_name), "
                "last_preview=COALESCE(?, last_preview), "
                "unread_count=?, last_activity=?, "
                "last_seen_msg_id=COALESCE(?, last_seen_msg_id), "
                "updated_at=?, deleted_at=NULL WHERE id=?",
                (encrypt_field(contact_handle, key), handle_hash,
                 encrypt_field(contact_name, key), encrypt_field(preview, key),
                 new_unread, now, encrypt_field(last_seen_msg_id, key), now, tid),
            )
        await db.commit()
    result = await get_thread(config, user_id, tid)
    if result is None:
        # Explicit guard, not `assert` — asserts vanish under `python -O`.
        raise RuntimeError(f"thread upsert readback failed (thread={tid})")
    return result


async def get_thread(config: Config, user_id: str, thread_id: str) -> dict | None:
    """Return the decrypted thread dict, or None if not found.

    Note: soft-deleted threads are returned (deleted_at is set but row exists).
    This is intentional — the upsert revival path relies on reading deleted rows.
    Callers wanting only live threads should use ``list_threads``.
    """
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT * FROM channel_threads WHERE id=? AND user_id=?",
            (thread_id, user_id),
        )
        row = await cur.fetchone()
    return _row_to_dict(row, key) if row else None


async def list_threads(
    config: Config,
    user_id: str,
    channel: str | None = None,
) -> list[dict]:
    """List live (non-deleted) threads for a user, ordered newest-first.

    Pass ``channel`` to filter to a single channel (e.g. "whatsapp").
    """
    key = await get_user_dek(config, user_id)
    sql = "SELECT * FROM channel_threads WHERE user_id=? AND deleted_at IS NULL"
    params: list = [user_id]
    if channel:
        sql += " AND channel=?"
        params.append(channel)
    sql += " ORDER BY last_activity DESC"
    async with db_session(config) as db:
        cur = await db.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return [_row_to_dict(r, key) for r in rows]


async def set_thread_instruction(
    config: Config, user_id: str, thread_id: str, instruction: str | None,
) -> bool:
    """Set (or clear with ``None``/empty) the per-contact standing instruction.

    The agent executes it as a real turn whenever THIS contact writes —
    layered on top of the channel-wide watcher auto_reply. Stored encrypted.
    Returns True when the thread row was found and updated.
    """
    key = await get_user_dek(config, user_id)
    value = encrypt_field(instruction, key) if (instruction or "").strip() else None
    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE channel_threads SET instruction=?, updated_at=? "
            "WHERE id=? AND user_id=? AND deleted_at IS NULL",
            (value, _now(), thread_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def set_thread_contact_name(
    config: Config, user_id: str, thread_id: str, name: str,
) -> bool:
    """Rename the thread's contact display name (encrypted). True if updated."""
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE channel_threads SET contact_name=?, updated_at=? "
            "WHERE id=? AND user_id=? AND deleted_at IS NULL",
            (encrypt_field(name, key), _now(), thread_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_instruction_for_handle(
    config: Config, user_id: str, channel: str, contact_handle: str,
) -> dict | None:
    """Heartbeat lookup: the live thread + instruction for one contact handle.

    Returns ``{"thread_id", "instruction", "contact_name"}`` when the contact
    has a standing instruction, else ``None``. Queries by the handle HMAC so
    the plaintext handle never hits an index.
    """
    key = await get_user_dek(config, user_id)
    handle_hash = _handle_hash(key, contact_handle)
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT * FROM channel_threads "
            "WHERE user_id=? AND channel=? AND contact_handle_hash=? "
            "AND deleted_at IS NULL",
            (user_id, channel, handle_hash),
        )
        row = await cur.fetchone()
    if row is None or "instruction" not in row.keys():
        return None
    instruction = decrypt_field(row["instruction"], key)
    if not instruction:
        return None
    return {
        "thread_id": row["id"],
        "instruction": instruction,
        "contact_name": decrypt_field(row["contact_name"], key),
    }


async def mark_thread_read(
    config: Config, user_id: str, thread_id: str
) -> bool:
    """Zero the unread_count for a thread. Returns True if a row was updated."""
    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE channel_threads SET unread_count=0, updated_at=? "
            "WHERE id=? AND user_id=?",
            (_now(), thread_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def soft_delete_legacy_duplicate(
    config: Config,
    user_id: str,
    *,
    channel: str,
    canonical_thread_id: str,
    legacy_handle: str,
) -> str | None:
    """Soft-delete a stale duplicate thread keyed on a LEGACY display-name handle.

    Before the chat_jid dedup fix, WhatsApp threads were keyed on the mutable
    pushName / sender name, so one contact left a stale row keyed on their
    display name alongside the correct JID-keyed thread — the "two rows for one
    contact" the user sees. Real threads are keyed by JID / phone / email, so a
    row whose handle EQUALS a display-name string is, in practice, only ever a
    legacy mis-keyed dupe.

    SAFETY: a name-keyed match is NOT a reliable identity proof (two contacts
    can share a display name), so this NEVER carries the legacy row's data
    forward, and it PRESERVES any legacy row the user configured with a standing
    instruction — deleting that could silently lose a deliberate setting. Only
    unconfigured stale dupes are auto-cleaned. Returns the soft-deleted id, or
    None when there is no distinct, unconfigured legacy row to remove.
    """
    legacy_handle = (legacy_handle or "").strip()
    if not legacy_handle:
        return None
    key = await get_user_dek(config, user_id)
    legacy_hash = _handle_hash(key, legacy_handle)
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id, instruction FROM channel_threads "
            "WHERE user_id=? AND channel=? AND contact_handle_hash=? "
            "AND deleted_at IS NULL AND id != ?",
            (user_id, channel, legacy_hash, canonical_thread_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        legacy_instruction = (
            decrypt_field(row["instruction"], key)
            if "instruction" in row.keys() else None
        )
        if legacy_instruction:
            # User configured this thread — leave it for them to manage.
            return None
        now = _now()
        await db.execute(
            "UPDATE channel_threads SET deleted_at=?, updated_at=? "
            "WHERE id=? AND user_id=?",
            (now, now, row["id"], user_id),
        )
        await db.commit()
    return row["id"]


async def delete_thread(
    config: Config, user_id: str, thread_id: str
) -> bool:
    """Soft-delete a thread (sets deleted_at). Returns True if updated."""
    now = _now()
    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE channel_threads SET deleted_at=?, updated_at=? "
            "WHERE id=? AND user_id=?",
            (now, now, thread_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_thread_changes(
    config: Config,
    user_id: str,
    since: str | None,
) -> dict:
    """Return a delta feed for offline-sync clients.

    Returns threads where ``updated_at > since`` (including soft-deleted
    tombstones so clients learn of deletes). When ``since`` is None, all
    rows are returned.

    Response shape::

        {
            "threads": [<live thread dicts>],
            "deleted": [<id, ...>],   # ids of soft-deleted rows
            "now":     "<server iso>",  # use as next ``since`` value
        }
    """
    key = await get_user_dek(config, user_id)
    now_iso = _now()
    sql = "SELECT * FROM channel_threads WHERE user_id=?"
    params: list = [user_id]
    if since is not None:
        sql += " AND updated_at > ?"
        params.append(since)
    sql += " ORDER BY updated_at ASC"
    async with db_session(config) as db:
        cur = await db.execute(sql, tuple(params))
        rows = await cur.fetchall()
    threads: list[dict] = []
    deleted: list[str] = []
    for r in rows:
        if r["deleted_at"] is not None:
            deleted.append(r["id"])
        else:
            threads.append(_row_to_dict(r, key))
    return {"threads": threads, "deleted": deleted, "now": now_iso}
