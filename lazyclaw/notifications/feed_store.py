"""In-app notification feed — the durable Notification Center store.

The mobile/web apps poll ``GET /api/notifications?since=`` to pull entries they
haven't seen yet, advancing their own ``since`` cursor (mirrors the offline-sync
``/changes`` feeds). Read-state is tracked server-side (``read_at``) so the tab
badge and the "mark read" actions survive a reinstall — unlike the transient OS
toasts this store replaces.

Storage threat model — plaintext vs encrypted:
  * ``id`` / ``kind`` / ``severity`` / ``dedup_key`` / ``read_at`` /
    ``created_at``  → PLAINTEXT (needed for the since-query, dedup lookup,
    unread badge, and client-side rendering of the notification type).
  * ``title`` / ``body`` / ``meta``  → user content, AES-256-GCM encrypted with
    the per-user DEK (same envelope as tasks/budgets). ``meta`` carries the
    structured ``deep_link`` + ``actions`` + legacy ``thread_ref``.
"""

from __future__ import annotations

import html as _html_mod
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

VALID_SEVERITIES: tuple[str, ...] = ("info", "normal", "important", "urgent")
DEFAULT_SEVERITY = "normal"

# A repeat notification carrying the same ``dedup_key`` that is still UNREAD and
# newer than this window collapses into the existing row (created_at refreshed +
# repeat_count bumped) instead of inserting a new one. Kills oscillating-watcher
# and repeat-nag spam in the Notification Center.
DEDUP_WINDOW = timedelta(minutes=30)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def strip_html(text: str) -> str:
    """Flatten Telegram-HTML markup to plain text for phone surfaces.

    Producers build ``<b>``/``<i>`` strings for Telegram's HTML parse mode;
    the phone's Notification Center and OS notifications render text
    verbatim, so a task heads-up used to show literally as
    ``⏰ <b>Medicine</b>``. Applied at :func:`record_notification` — the one
    choke point every feed writer passes through — so no individual funnel
    can leak markup again. Telegram delivery happens on separate code paths
    and keeps its HTML.
    """
    no_tags = re.sub(r"<[^>]+>", "", text or "")
    return _html_mod.unescape(no_tags).strip()


def strip_markdown(text: str) -> str:
    """Remove common Markdown formatting from a known-Markdown string.

    NOT applied at the record_notification choke point — the italic rule
    would mangle legitimate content like ``5 * 3 * 2``. Callers that KNOW
    their text is Markdown (the skill-push funnel, the Telegram notifier's
    plain-text fallbacks) opt in at their own seam.

    Closed pairs are stripped first; any orphan ``**`` runs left after a
    mid-stream truncation upstream are then collapsed, so the user never
    sees raw ``**foo`` leak through.
    """
    # Bold: **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'\1', text, flags=re.DOTALL)
    # Italic: *text* or _text_
    text = re.sub(r'\*(.+?)\*', r'\1', text, flags=re.DOTALL)
    # Strikethrough: ~~text~~
    text = re.sub(r'~~(.+?)~~', r'\1', text, flags=re.DOTALL)
    # Inline code: `text`
    text = re.sub(r'`(.+?)`', r'\1', text, flags=re.DOTALL)
    # Headers: ### text → text
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Links: [text](url) → text (url)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1 (\2)', text)
    # Bullet points: - text → • text
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    # Orphan bold/strike runs (truncation leftovers). `**` and `~~` never
    # appear as literal content in real chat output, so it's safe to drop.
    text = re.sub(r'\*\*+', '', text)
    text = re.sub(r'~~+', '', text)
    return text


def _normalize_severity(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in VALID_SEVERITIES else DEFAULT_SEVERITY


def _build_meta(
    meta: dict | None,
    deep_link: dict | None,
    actions: list | None,
) -> dict | None:
    """Merge the legacy ``meta`` sidecar with the structured deep_link/actions.

    Returns ``None`` when nothing is present so callers that pass no metadata
    still store a NULL meta column (back-compat with ``test_no_thread_ref``).
    """
    merged: dict = dict(meta) if isinstance(meta, dict) else {}
    if deep_link is not None:
        merged["deep_link"] = deep_link
    if actions is not None:
        merged["actions"] = actions
    return merged or None


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


async def _find_dedup_row(
    db, user_id: str, dedup_key: str, cutoff_iso: str
) -> tuple[str, str | None] | None:
    """Return ``(id, meta)`` of the newest UNREAD row to collapse into, or None."""
    cur = await db.execute(
        "SELECT id, meta FROM notifications "
        "WHERE user_id = ? AND dedup_key = ? AND read_at IS NULL "
        "AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, dedup_key, cutoff_iso),
    )
    row = await cur.fetchone()
    return (row[0], row[1]) if row else None


async def record_notification(
    config: Config,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    meta: dict | None = None,
    *,
    severity: str = DEFAULT_SEVERITY,
    deep_link: dict | None = None,
    actions: list | None = None,
    dedup_key: str | None = None,
    dedup_window: timedelta | None = None,
) -> dict:
    """Persist one feed entry (title/body/meta encrypted). Returns the decrypted dict.

    ``kind`` is a plaintext type tag (``channel_message`` / ``task_reminder`` /
    ``watcher_hit`` / ``done`` / …). ``severity`` (``info|normal|important|
    urgent``) drives client rendering. ``deep_link`` is a structured tap target
    (``{"type","id","channel"}``) and ``actions`` a list of button specs — both
    ride inside the encrypted ``meta`` sidecar. When ``dedup_key`` matches a
    recent unread row, that row is refreshed in place (``repeat_count`` bumped)
    rather than appended, so repeats collapse instead of spamming.
    """
    key = await get_user_dek(config, user_id)
    created_at = _now()
    safe_kind = (kind or "info").strip() or "info"
    safe_sev = _normalize_severity(severity)
    merged_meta = _build_meta(meta, deep_link, actions)
    # Phone surfaces render verbatim — never let Telegram-HTML through.
    title = strip_html(title)
    body = strip_html(body)

    async with db_session(config) as db:
        # ── dedup: collapse a recent unread repeat into the existing row ──
        # A caller may widen the window when its repeats span more than the
        # default 30min — the task nag ladder stretches ~45min-3h across one
        # occurrence, so the daemon passes hours and the whole ladder folds
        # into ONE row instead of one row per gap > window.
        if dedup_key:
            cutoff_iso = (
                datetime.now(timezone.utc) - (dedup_window or DEDUP_WINDOW)
            ).isoformat()
            existing = await _find_dedup_row(db, user_id, dedup_key, cutoff_iso)
            if existing is not None:
                existing_id, existing_meta_raw = existing
                prior = _decode_meta(existing_meta_raw, key) or {}
                repeat = int(prior.get("repeat_count", 1)) + 1
                bump_meta = dict(merged_meta) if merged_meta else {}
                bump_meta["repeat_count"] = repeat
                await db.execute(
                    "UPDATE notifications SET kind=?, title=?, body=?, meta=?, "
                    "severity=?, created_at=? WHERE id=?",
                    (
                        safe_kind,
                        encrypt(title or "", key),
                        encrypt(body or "", key),
                        encrypt(json.dumps(bump_meta), key),
                        safe_sev,
                        created_at,
                        existing_id,
                    ),
                )
                await db.commit()
                return {
                    "id": existing_id,
                    "kind": safe_kind,
                    "severity": safe_sev,
                    "title": title or "",
                    "body": body or "",
                    "meta": bump_meta,
                    "deep_link": deep_link,
                    "actions": actions,
                    "dedup_key": dedup_key,
                    "read_at": None,
                    "created_at": created_at,
                    "repeat_count": repeat,
                }

        # ── fresh insert ──
        notif_id = str(uuid4())
        enc_meta = (
            encrypt(json.dumps(merged_meta), key) if merged_meta is not None else None
        )
        await db.execute(
            "INSERT INTO notifications "
            "(id, user_id, kind, title, body, meta, severity, dedup_key, "
            "read_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                notif_id,
                user_id,
                safe_kind,
                encrypt(title or "", key),
                encrypt(body or "", key),
                enc_meta,
                safe_sev,
                dedup_key,
                None,
                created_at,
            ),
        )
        await db.commit()

    return {
        "id": notif_id,
        "kind": safe_kind,
        "severity": safe_sev,
        "title": title or "",
        "body": body or "",
        "meta": merged_meta,
        "deep_link": deep_link,
        "actions": actions,
        "dedup_key": dedup_key,
        "read_at": None,
        "created_at": created_at,
        "repeat_count": 1,
    }


def _row_to_dict(row, key: bytes) -> dict:
    meta = _decode_meta(row[4], key)
    return {
        "id": row[0],
        "kind": row[1],
        "title": decrypt_field(row[2], key) or "",
        "body": decrypt_field(row[3], key) or "",
        "meta": meta,
        "severity": row[5] or DEFAULT_SEVERITY,
        "read_at": row[6],
        "created_at": row[7],
        "deep_link": (meta or {}).get("deep_link"),
        "actions": (meta or {}).get("actions"),
        "repeat_count": int((meta or {}).get("repeat_count", 1)),
    }


async def get_notifications_since(
    config: Config,
    user_id: str,
    since_iso: str | None = None,
) -> dict:
    """Delta feed of notifications created after ``since_iso`` (decrypted).

    ``now`` is stamped BEFORE the query so a client that adopts it as the next
    ``since`` never misses a row inserted during the read window. When
    ``since_iso`` is omitted, every row for the user is returned (full sync).

    Response shape::

        {
          "notifications": [
             {id, kind, severity, title, body, meta, deep_link, actions,
              read_at, repeat_count, created_at}, ...
          ],
          "unread": <int>,        # total unread across the whole store
          "now": "<server iso>",  # use as next `since`
        }
    """
    key = await get_user_dek(config, user_id)
    now_iso = _now()
    cols = "id, kind, title, body, meta, severity, read_at, created_at"

    async with db_session(config) as db:
        if since_iso:
            cur = await db.execute(
                f"SELECT {cols} FROM notifications "
                "WHERE user_id = ? AND created_at > ? ORDER BY created_at ASC",
                (user_id, since_iso),
            )
        else:
            cur = await db.execute(
                f"SELECT {cols} FROM notifications "
                "WHERE user_id = ? ORDER BY created_at ASC",
                (user_id,),
            )
        rows = await cur.fetchall()
        unread_cur = await db.execute(
            "SELECT COUNT(*) FROM notifications "
            "WHERE user_id = ? AND read_at IS NULL",
            (user_id,),
        )
        unread_row = await unread_cur.fetchone()
        unread = unread_row[0] if unread_row else 0

    return {
        "notifications": [_row_to_dict(r, key) for r in rows],
        "unread": int(unread),
        "now": now_iso,
    }


async def get_unread_count(config: Config, user_id: str) -> int:
    """Count of unread notifications for the tab badge."""
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM notifications "
            "WHERE user_id = ? AND read_at IS NULL",
            (user_id,),
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def mark_read(config: Config, user_id: str, ids: list[str]) -> int:
    """Mark specific notifications read (scoped to the user). Returns rows updated."""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return 0
    read_at = _now()
    placeholders = ",".join("?" for _ in ids)
    async with db_session(config) as db:
        cur = await db.execute(
            f"UPDATE notifications SET read_at = ? "
            f"WHERE user_id = ? AND read_at IS NULL AND id IN ({placeholders})",
            (read_at, user_id, *ids),
        )
        await db.commit()
        return cur.rowcount if cur.rowcount is not None else 0


async def mark_all_read(config: Config, user_id: str) -> int:
    """Mark every unread notification for the user read. Returns rows updated."""
    read_at = _now()
    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE notifications SET read_at = ? "
            "WHERE user_id = ? AND read_at IS NULL",
            (read_at, user_id),
        )
        await db.commit()
        return cur.rowcount if cur.rowcount is not None else 0
