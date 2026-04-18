"""Key rotation for LazyBrain notes.

This is a **nonce-rotation** pass: every note is decrypted with the user's
current DEK and re-encrypted with a fresh AES-GCM nonce (same DEK, new
randomness). That's the right hygiene layer for ``--scope lazybrain`` —
full DEK rotation also needs to touch every other encrypted table, and
lives under the broader ``rotate_server_secret`` flow in
``lazyclaw.crypto.key_manager``.

Writes a ``keys_rotated`` entry to ``audit_log`` per user.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt_field, user_aad
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


async def rotate_user_notes(
    config: Config,
    user_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Re-roll AES-GCM nonces for every note owned by ``user_id``."""
    dek = await get_user_dek(config, user_id)
    title_aad = user_aad(user_id, "notes:title")
    content_aad = user_aad(user_id, "notes:content")

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, title, content FROM notes WHERE user_id = ?",
            (user_id,),
        )
        all_rows = await rows.fetchall()

    rotated = 0
    failed = 0
    for note_id, enc_title, enc_content in all_rows:
        try:
            plain_title = decrypt_field(enc_title, dek, title_aad, fallback="")
            plain_content = decrypt_field(enc_content, dek, content_aad, fallback="")
            new_title = encrypt_field(plain_title, dek, title_aad)
            new_content = encrypt_field(plain_content, dek, content_aad)
            if dry_run:
                rotated += 1
                continue
            async with db_session(config) as db:
                await db.execute(
                    "UPDATE notes SET title = ?, content = ?, "
                    "updated_at = datetime('now') "
                    "WHERE id = ? AND user_id = ?",
                    (new_title, new_content, note_id, user_id),
                )
                await db.commit()
            rotated += 1
        except Exception as exc:
            failed += 1
            logger.warning(
                "Rotation failed for note %s of user %s: %s",
                note_id,
                user_id,
                exc,
            )

    if not dry_run and rotated > 0:
        await _audit(config, user_id, rotated, failed)

    return {
        "user_id": user_id,
        "scope": "lazybrain",
        "rotated": rotated,
        "failed": failed,
        "dry_run": dry_run,
    }


async def rotate_all_users(
    config: Config, *, dry_run: bool = False
) -> list[dict]:
    """Rotate every user's notes. Returns per-user reports."""
    async with db_session(config) as db:
        rows = await db.execute("SELECT id FROM users")
        user_ids = [row[0] for row in await rows.fetchall()]

    results: list[dict] = []
    for uid in user_ids:
        try:
            results.append(await rotate_user_notes(config, uid, dry_run=dry_run))
        except Exception as exc:
            logger.error("Rotation aborted for user %s: %s", uid, exc)
            results.append({"user_id": uid, "error": str(exc)})
    return results


async def _audit(
    config: Config, user_id: str, rotated: int, failed: int
) -> None:
    try:
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO audit_log "
                "(id, user_id, action, skill_name, result_summary, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    user_id,
                    "keys_rotated",
                    "lazybrain_rotation",
                    f"rotated={rotated} failed={failed}",
                    "system",
                ),
            )
            await db.commit()
    except Exception as exc:
        logger.debug("Rotation audit write failed: %s", exc)
