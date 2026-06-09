"""Encrypted persistence + scheduling for autonomous conversation tasks."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt_field, decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

# Fields that must be encrypted at rest (including transcript_json)
_ENC = {"contact_name", "goal", "completion_criteria", "transcript_json", "result", "error"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row, key: bytes) -> dict:
    transcript_raw = decrypt_field(row["transcript_json"], key) if row["transcript_json"] else None
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "channel": row["channel"],
        "contact_handle": row["contact_handle"],
        "contact_name": decrypt_field(row["contact_name"], key),
        "goal": decrypt_field(row["goal"], key),
        "completion_criteria": decrypt_field(row["completion_criteria"], key),
        "status": row["status"],
        "transcript": json.loads(transcript_raw) if transcript_raw else [],
        "iteration": row["iteration"],
        "max_iterations": row["max_iterations"],
        "poll_interval": row["poll_interval"],
        "next_poll_at": row["next_poll_at"],
        "created_at": row["created_at"],
        "last_activity_at": row["last_activity_at"],
        "expires_at": row["expires_at"],
        "result": decrypt_field(row["result"], key),
        "error": decrypt_field(row["error"], key),
        "approval_id": row["approval_id"],
    }


async def create_conversation(
    config: Config,
    user_id: str,
    *,
    channel: str,
    contact_handle: str,
    contact_name: str | None,
    goal: str,
    completion_criteria: str | None = None,
    max_iterations: int = 20,
    poll_interval: int = 60,
    expires_at: str | None = None,
) -> dict:
    """Create a new conversation task, returning the decrypted dict."""
    key = await get_user_dek(config, user_id)
    cid = str(uuid4())
    now = _now()
    empty_transcript = encrypt_field(json.dumps([]), key)
    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO conversation_tasks "
            "(id,user_id,channel,contact_handle,contact_name,goal,completion_criteria,"
            "status,transcript_json,iteration,max_iterations,poll_interval,next_poll_at,"
            "created_at,last_activity_at,expires_at,result,error,approval_id) "
            "VALUES (?,?,?,?,?,?,?,'drafting',?,0,?,?,?,?,?,?,NULL,NULL,NULL)",
            (
                cid, user_id, channel, contact_handle,
                encrypt_field(contact_name, key),
                encrypt_field(goal, key),
                encrypt_field(completion_criteria, key),
                empty_transcript,
                max_iterations, poll_interval, now,
                now, now, expires_at,
            ),
        )
        await db.commit()
    result = await get_conversation(config, user_id, cid)
    assert result is not None  # just inserted
    return result


async def get_conversation(config: Config, user_id: str, cid: str) -> dict | None:
    """Return the decrypted conversation dict, or None if not found."""
    key = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT * FROM conversation_tasks WHERE id=? AND user_id=?",
            (cid, user_id),
        )
        row = await cur.fetchone()
    return _row_to_dict(row, key) if row else None


async def update_conversation(
    config: Config,
    user_id: str,
    cid: str,
    *,
    append_transcript: dict | None = None,
    **fields,
) -> dict | None:
    """Update fields on a conversation task, optionally appending a transcript entry.

    Encrypted columns in ``_ENC`` are re-encrypted on update.
    ``append_transcript`` is a single dict appended to the existing transcript list.
    Returns the refreshed decrypted dict, or None if the row was not found.
    """
    key = await get_user_dek(config, user_id)
    current = await get_conversation(config, user_id, cid)
    if current is None:
        return None

    sets: list[str] = []
    params: list = []

    for col, val in fields.items():
        if col in _ENC:
            val = encrypt_field(val, key) if val is not None else None
        sets.append(f"{col}=?")
        params.append(val)

    if append_transcript is not None:
        updated_transcript = current["transcript"] + [append_transcript]
        sets.append("transcript_json=?")
        params.append(encrypt_field(json.dumps(updated_transcript), key))

    sets.append("last_activity_at=?")
    params.append(_now())
    params.extend([cid, user_id])

    async with db_session(config) as db:
        await db.execute(
            f"UPDATE conversation_tasks SET {', '.join(sets)} WHERE id=? AND user_id=?",
            tuple(params),
        )
        await db.commit()
    return await get_conversation(config, user_id, cid)


async def list_due(config: Config, now_iso: str) -> list[dict]:
    """Return all conversations across all users that are due for a step.

    A conversation is due when:
    - status is 'drafting' or 'running'
    - next_poll_at is set and <= now_iso

    Each returned dict includes ``user_id`` so the conversation runner can
    thread the correct user through specialist calls.
    """
    async with db_session(config) as db:
        cur = await db.execute(
            "SELECT id, user_id FROM conversation_tasks "
            "WHERE status IN ('drafting','running') "
            "AND next_poll_at IS NOT NULL "
            "AND next_poll_at <= ?",
            (now_iso,),
        )
        rows = await cur.fetchall()

    out: list[dict] = []
    for r in rows:
        conv = await get_conversation(config, r["user_id"], r["id"])
        if conv is not None:
            out.append(conv)
    return out
