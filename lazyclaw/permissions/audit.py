"""Audit log — records all tool executions, approvals, and permission changes."""

from __future__ import annotations

import hashlib
import json
import logging
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import encrypt
from lazyclaw.db.connection import db_session
from lazyclaw.permissions.models import AuditEntry

logger = logging.getLogger(__name__)

# Valid audit actions
VALID_ACTIONS = {
    "tool_executed",
    "tool_denied",
    "tool_approved",
    "tool_expired",
    "permission_changed",
    "login",
    "logout",
}


def _hash_arguments(arguments: dict | None) -> str | None:
    """SHA-256 hash of arguments for privacy-safe storage."""
    if not arguments:
        return None
    raw = json.dumps(arguments, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _truncate_result(result: str | None, max_length: int = 200) -> str | None:
    """Truncate result to max_length for summary storage."""
    if not result:
        return None
    if len(result) <= max_length:
        return result
    return result[:max_length] + "..."


async def log_action(
    config: Config,
    user_id: str,
    action: str,
    skill_name: str | None = None,
    arguments: dict | None = None,
    result_summary: str | None = None,
    approval_id: str | None = None,
    source: str = "agent",
    ip_address: str | None = None,
) -> None:
    """Write an audit log entry. Fire-and-forget — never raises."""
    try:
        entry_id = str(uuid4())
        args_hash = _hash_arguments(arguments)

        # Encrypt result summary if present
        encrypted_summary = None
        if result_summary:
            key = await get_user_dek(config, user_id)
            truncated = _truncate_result(result_summary)
            encrypted_summary = encrypt(truncated, key)

        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO audit_log (id, user_id, action, skill_name, arguments_hash, "
                "result_summary, approval_id, source, ip_address) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, user_id, action, skill_name, args_hash,
                 encrypted_summary, approval_id, source, ip_address),
            )
            await db.commit()
    except Exception as exc:
        # Fire-and-forget: log error but never propagate
        logger.error("Failed to write audit log: %s", exc)


def _row_to_entry(row) -> AuditEntry:
    """Convert a DB row to a frozen AuditEntry."""
    return AuditEntry(
        id=row[0],
        user_id=row[1],
        action=row[2],
        skill_name=row[3],
        arguments_hash=row[4],
        result_summary=row[5],
        approval_id=row[6],
        source=row[7],
        ip_address=row[8],
        created_at=row[9],
    )


async def query_log(
    config: Config,
    user_id: str,
    action_filter: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[AuditEntry]:
    """Query audit log entries for a user."""
    conditions = ["user_id = ?"]
    params: list = [user_id]

    if action_filter:
        conditions.append("action = ?")
        params.append(action_filter)

    if since:
        conditions.append("created_at >= ?")
        params.append(since)

    where = " AND ".join(conditions)
    query = (
        f"SELECT id, user_id, action, skill_name, arguments_hash, "
        f"result_summary, approval_id, source, ip_address, created_at "
        f"FROM audit_log WHERE {where} "
        f"ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)

    async with db_session(config) as db:
        cursor = await db.execute(query, tuple(params))
        rows = await cursor.fetchall()

    return [_row_to_entry(row) for row in rows]


async def cleanup_old_entries(config: Config, days: int = 90) -> int:
    """Delete audit log entries older than N days. Returns count deleted."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "DELETE FROM audit_log WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await db.commit()
        return cursor.rowcount
