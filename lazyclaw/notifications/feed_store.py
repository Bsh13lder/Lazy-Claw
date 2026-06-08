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
) -> dict:
    """Persist one feed entry (title/body encrypted). Returns the decrypted dict.

    ``kind`` is a plaintext type tag (e.g. ``push`` / ``done`` /
    ``background_failed``); ``title`` and ``body`` are user content.
    """
    key = await get_user_dek(config, user_id)
    notif_id = str(uuid4())
    created_at = _now()
    safe_kind = (kind or "info").strip() or "info"
    enc_title = encrypt(title or "", key)
    enc_body = encrypt(body or "", key)

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO notifications "
            "(id, user_id, kind, title, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (notif_id, user_id, safe_kind, enc_title, enc_body, created_at),
        )
        await db.commit()

    return {
        "id": notif_id,
        "kind": safe_kind,
        "title": title or "",
        "body": body or "",
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
                {"id", "kind", "title", "body", "created_at"}, ...
            ],
            "now": "<server iso>",   # use as next `since`
        }
    """
    key = await get_user_dek(config, user_id)
    now_iso = _now()

    if since_iso:
        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id, kind, title, body, created_at FROM notifications "
                "WHERE user_id = ? AND created_at > ? "
                "ORDER BY created_at ASC",
                (user_id, since_iso),
            )
            rows = await cur.fetchall()
    else:
        async with db_session(config) as db:
            cur = await db.execute(
                "SELECT id, kind, title, body, created_at FROM notifications "
                "WHERE user_id = ? "
                "ORDER BY created_at ASC",
                (user_id,),
            )
            rows = await cur.fetchall()

    notifications = [
        {
            "id": r[0],
            "kind": r[1],
            "title": decrypt_field(r[2], key) or "",
            "body": decrypt_field(r[3], key) or "",
            "created_at": r[4],
        }
        for r in rows
    ]

    return {"notifications": notifications, "now": now_iso}
