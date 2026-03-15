"""Approval request management — create, approve, deny, expire."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, encrypt
from lazyclaw.db.connection import db_session
from lazyclaw.permissions.models import ApprovalRequest

logger = logging.getLogger(__name__)


def _row_to_approval(row) -> ApprovalRequest:
    """Convert a DB row to a frozen ApprovalRequest."""
    return ApprovalRequest(
        id=row[0],
        user_id=row[1],
        skill_name=row[2],
        arguments=row[3] or "",
        status=row[4],
        source=row[5],
        decided_by=row[6],
        decided_at=row[7],
        expires_at=row[8],
        created_at=row[9],
    )


_APPROVAL_COLUMNS = (
    "id, user_id, skill_name, arguments, status, source, "
    "decided_by, decided_at, expires_at, created_at"
)


async def create_approval(
    config: Config,
    user_id: str,
    skill_name: str,
    arguments: dict | None = None,
    source: str = "agent",
    timeout_seconds: int = 300,
    chat_session_id: str | None = None,
) -> ApprovalRequest:
    """Create a pending approval request."""
    approval_id = str(uuid4())
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=timeout_seconds)).isoformat()

    # Encrypt arguments
    key = derive_server_key(config.server_secret, user_id)
    args_str = json.dumps(arguments) if arguments else "{}"
    encrypted_args = encrypt(args_str, key)

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO approval_requests "
            "(id, user_id, skill_name, arguments, status, source, chat_session_id, expires_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (approval_id, user_id, skill_name, encrypted_args, source, chat_session_id, expires_at),
        )
        await db.commit()

    return ApprovalRequest(
        id=approval_id,
        user_id=user_id,
        skill_name=skill_name,
        arguments=encrypted_args,
        status="pending",
        source=source,
        decided_by=None,
        decided_at=None,
        expires_at=expires_at,
        created_at=now.isoformat(),
    )


async def get_pending(config: Config, user_id: str) -> list[ApprovalRequest]:
    """Get all pending approval requests for a user. Auto-expires old ones."""
    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        # Expire old requests
        await db.execute(
            "UPDATE approval_requests SET status = 'expired' "
            "WHERE user_id = ? AND status = 'pending' AND expires_at < ?",
            (user_id, now),
        )
        await db.commit()

        # Fetch remaining pending
        cursor = await db.execute(
            f"SELECT {_APPROVAL_COLUMNS} "
            "FROM approval_requests WHERE user_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()

    return [_row_to_approval(row) for row in rows]


async def approve_request(
    config: Config, approval_id: str, decided_by: str
) -> ApprovalRequest | None:
    """Approve a pending request. Returns updated request or None if not found."""
    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE approval_requests SET status = 'approved', decided_by = ?, decided_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (decided_by, now, approval_id),
        )
        await db.commit()

        cursor = await db.execute(
            f"SELECT {_APPROVAL_COLUMNS} FROM approval_requests WHERE id = ?",
            (approval_id,),
        )
        row = await cursor.fetchone()

    if not row:
        return None
    return _row_to_approval(row)


async def deny_request(
    config: Config, approval_id: str, decided_by: str
) -> ApprovalRequest | None:
    """Deny a pending request. Returns updated request or None if not found."""
    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE approval_requests SET status = 'denied', decided_by = ?, decided_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (decided_by, now, approval_id),
        )
        await db.commit()

        cursor = await db.execute(
            f"SELECT {_APPROVAL_COLUMNS} FROM approval_requests WHERE id = ?",
            (approval_id,),
        )
        row = await cursor.fetchone()

    if not row:
        return None
    return _row_to_approval(row)


async def get_request(config: Config, approval_id: str) -> ApprovalRequest | None:
    """Get a single approval request by ID."""
    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {_APPROVAL_COLUMNS} FROM approval_requests WHERE id = ?",
            (approval_id,),
        )
        row = await cursor.fetchone()

    if not row:
        return None
    return _row_to_approval(row)
