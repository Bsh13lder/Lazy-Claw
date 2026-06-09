"""Encrypted store for cross-channel inbox threads (metadata only —
message bodies are read live via ChannelGateway)."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt_field, decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row, key: bytes) -> dict:
    return {
        "id": row["id"],
        "channel": row["channel"],
        "contact_handle": row["contact_handle"],
        "contact_name": decrypt_field(row["contact_name"], key),
        "last_preview": decrypt_field(row["last_preview"], key),
        "unread_count": row["unread_count"],
        "last_activity": row["last_activity"],
        "last_seen_msg_id": decrypt_field(row["last_seen_msg_id"], key),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
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

    Deduplication key: (user_id, channel, contact_handle).
    When a thread already exists, unread_count is incremented if
    ``increment_unread`` is True; encrypted fields are refreshed.
    A soft-deleted thread is revived (deleted_at=NULL) on upsert.
    """
    key = await get_user_dek(config, user_id)
    now = _now()
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id, unread_count FROM channel_threads "
            "WHERE user_id=? AND channel=? AND contact_handle=?",
            (user_id, channel, contact_handle),
        )
        existing = await cur.fetchone()
        if existing is None:
            tid = str(uuid4())
            await db.execute(
                "INSERT INTO channel_threads "
                "(id,user_id,channel,contact_handle,contact_name,last_preview,"
                "unread_count,last_activity,last_seen_msg_id,created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    tid, user_id, channel, contact_handle,
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
                "contact_name=COALESCE(?, contact_name), "
                "last_preview=COALESCE(?, last_preview), "
                "unread_count=?, last_activity=?, "
                "last_seen_msg_id=COALESCE(?, last_seen_msg_id), "
                "updated_at=?, deleted_at=NULL WHERE id=?",
                (encrypt_field(contact_name, key), encrypt_field(preview, key),
                 new_unread, now, encrypt_field(last_seen_msg_id, key), now, tid),
            )
        await db.commit()
    result = await get_thread(config, user_id, tid)
    assert result is not None  # just upserted
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
