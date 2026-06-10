"""In-app notification feed — encrypted-at-rest mirror of what was (or would
have been) pushed to Telegram.

The mobile app polls ``GET /api/notifications?since=`` to pull entries it
hasn't seen yet, advancing its own ``since`` cursor (mirrors the offline-sync
``/changes`` feeds — there is no server-side ack).

Storage threat model — plaintext vs encrypted:
  * ``id`` / ``kind`` / ``created_at``  → PLAINTEXT (needed for the since-query
    + client-side rendering of the notification type).
  * ``title`` / ``body``                → user content, AES-256-GCM encrypted
    with the per-user DEK (same envelope as tasks/budgets).
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def record_notification(
    config: Config,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    meta: dict | None = None,
) -> dict:
    """Persist one feed entry (title/body/meta encrypted). Returns the decrypted dict.

    ``kind`` is a plaintext type tag (e.g. ``push`` / ``done`` /
    ``background_failed``); ``title`` and ``body`` are user content.
    ``meta`` is an optional dict that is JSON-serialised and AES-encrypted
    before storage (e.g. ``{"thread_ref": {"channel": "whatsapp", ...}}``
    for tap-to-open deep links on the mobile app).
    """
    key = await get_user_dek(config, user_id)
    notif_id = str(uuid4())
    created_at = _now()
    safe_kind = (kind or "info").strip() or "info"
    enc_title = encrypt(title or "", key)
    enc_body = encrypt(body or "", key)
    enc_meta = encrypt(json.dumps(meta), key) if meta is not None else None

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO notifications "
            "(id, user_id, kind, title, body, meta, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (notif_id, user_id, safe_kind, enc_title, enc_body, enc_meta, created_at),
        )
        await db.commit()

    return {
        "id": notif_id,
        "kind": safe_kind,
        "title": title or "",
        "body": body or "",
        "meta": meta,
        "created_at": created_at,
    }


async def get_notifications_since(
    config: Config,
    user_id: str,
    since_iso: str | None = None,
) -> dict:
    """Delta feed of notifications created after ``since_iso`` (decrypted).

    ``now`` is stamped BEFORE the query so a client that adopts it as the next
    ``since`` never misses a row inserted during the read window (mirrors the
    ``get_*_changes`` contract). When ``since_iso`` is omitted, every row for
    the user is returned (full sync).

    Response shape::

        {
            "notifications": [
                {"id", "kind", "title", "body", "meta", "created_at"}, ...
            ],
            "now": "<server iso>",   # use as next `since`
        }
    """
    key = await get_user_dek(config, user_id)
    now_iso = _now()

    if since_iso:
        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id, kind, title, body, meta, created_at FROM notifications "
                "WHERE user_id = ? AND created_at > ? "
                "ORDER BY created_at ASC",
                (user_id, since_iso),
            )
            rows = await cur.fetchall()
    else:
        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id, kind, title, body, meta, created_at FROM notifications "
                "WHERE user_id = ? "
                "ORDER BY created_at ASC",
                (user_id,),
            )
            rows = await cur.fetchall()

    def _decode_meta(raw: str | None, key: bytes) -> dict | None:
        if raw is None:
            return None
        decrypted = decrypt_field(raw, key)
        if not decrypted:
            return None
        try:
            return json.loads(decrypted)
        except (json.JSONDecodeError, ValueError):
            logger.warning("notification meta could not be decoded; returning None")
            return None

    notifications = [
        {
            "id": r[0],
            "kind": r[1],
            "title": decrypt_field(r[2], key) or "",
            "body": decrypt_field(r[3], key) or "",
            "meta": _decode_meta(r[4], key),
            "created_at": r[5],
        }
        for r in rows
    ]

    return {"notifications": notifications, "now": now_iso}
