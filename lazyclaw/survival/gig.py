"""Gig lifecycle tracking: CRUD for survival_gigs table.

States: found → applied → hired → working → review → delivered → invoiced → paid
Also: rejected, needs_work (loops back to working).

All user-facing fields encrypted at rest via get_user_dek().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import (
    decrypt_field,
    encrypt,
)
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({
    "found", "applied", "hired", "working", "review",
    "delivered", "invoiced", "paid", "rejected", "needs_work",
})

ENCRYPTED_FIELDS = frozenset({
    "title", "description", "client_name",
    "proposal_text", "workspace_path", "deliverable_summary",
})

GIG_COLUMNS = [
    "id", "user_id", "platform", "external_job_id",
    "title", "description", "budget", "budget_value",
    "client_name", "url", "status", "proposal_text",
    "workspace_path", "deliverable_summary", "invoice_id",
    "amount_earned", "created_at", "updated_at",
]

GIG_SELECT = ", ".join(GIG_COLUMNS)


@dataclass(frozen=True)
class Gig:
    """Immutable gig record."""

    id: str
    user_id: str
    platform: str
    external_job_id: str
    title: str
    description: str
    budget: str
    budget_value: float
    client_name: str
    url: str
    status: str
    proposal_text: str
    workspace_path: str
    deliverable_summary: str
    invoice_id: str
    amount_earned: float
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enc(value: str | None, key: bytes) -> str | None:
    return encrypt(value, key) if value else None


def _dec(value: str | None, key: bytes) -> str | None:
    if value is None:
        return ""
    return decrypt_field(value, key, fallback="")


def _row_to_gig(row, key: bytes) -> Gig:
    values = {}
    for i, col in enumerate(GIG_COLUMNS):
        val = row[i]
        if col in ENCRYPTED_FIELDS:
            val = _dec(val, key)
        values[col] = val
    return Gig(**values)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_gig(
    config: Config,
    user_id: str,
    *,
    platform: str,
    title: str,
    description: str = "",
    budget: str = "",
    budget_value: float = 0.0,
    client_name: str = "",
    url: str = "",
    status: str = "found",
    external_job_id: str = "",
    proposal_text: str = "",
) -> str:
    """Create a new gig record. Returns the gig ID."""
    key = await get_user_dek(config, user_id)
    gig_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO survival_gigs "
            "(id, user_id, platform, external_job_id, title, description, "
            "budget, budget_value, client_name, url, status, proposal_text, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                gig_id, user_id, platform, external_job_id,
                _enc(title, key), _enc(description, key),
                budget, budget_value,
                _enc(client_name, key), url, status,
                _enc(proposal_text, key), now, now,
            ),
        )
        await db.commit()

    logger.info("Created gig %s (%s) for user %s", gig_id[:8], status, user_id[:8])
    return gig_id


async def update_gig_status(
    config: Config,
    user_id: str,
    gig_id: str,
    new_status: str,
    **extra_fields: str | float | None,
) -> bool:
    """Transition a gig to a new status. Returns True on success."""
    if new_status not in VALID_STATUSES:
        logger.warning("Invalid gig status: %s", new_status)
        return False

    key = await get_user_dek(config, user_id)
    now = datetime.now(timezone.utc).isoformat()

    # Build SET clause from extra fields
    set_parts = ["status = ?", "updated_at = ?"]
    params: list = [new_status, now]

    for field, value in extra_fields.items():
        if field not in GIG_COLUMNS or field in ("id", "user_id", "created_at"):
            continue
        if field in ENCRYPTED_FIELDS and value:
            value = _enc(str(value), key)
        set_parts.append(f"{field} = ?")
        params.append(value)

    params.extend([gig_id, user_id])

    async with db_session(config) as db:
        result = await db.execute(
            f"UPDATE survival_gigs SET {', '.join(set_parts)} "
            "WHERE id = ? AND user_id = ?",
            tuple(params),
        )
        await db.commit()
        updated = result.rowcount > 0

    if updated:
        logger.info("Gig %s → %s", gig_id[:8], new_status)
    return updated


async def get_gig(config: Config, user_id: str, gig_id: str) -> Gig | None:
    """Load a single gig by ID."""
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {GIG_SELECT} FROM survival_gigs "
            "WHERE id = ? AND user_id = ?",
            (gig_id, user_id),
        )
        row = await cursor.fetchone()

    return _row_to_gig(row, key) if row else None


async def list_gigs(
    config: Config,
    user_id: str,
    status: str | None = None,
    limit: int = 50,
) -> list[Gig]:
    """List gigs for a user, optionally filtered by status."""
    key = await get_user_dek(config, user_id)

    query = f"SELECT {GIG_SELECT} FROM survival_gigs WHERE user_id = ?"
    params: list = [user_id]

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    async with db_session(config) as db:
        cursor = await db.execute(query, tuple(params))
        rows = await cursor.fetchall()

    return [_row_to_gig(row, key) for row in rows]


async def get_gig_stats(config: Config, user_id: str) -> dict:
    """Get aggregate stats across all gigs for a user."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*), SUM(amount_earned) "
            "FROM survival_gigs WHERE user_id = ? GROUP BY status",
            (user_id,),
        )
        rows = await cursor.fetchall()

    stats: dict = {
        "applied": 0, "hired": 0, "working": 0, "review": 0,
        "delivered": 0, "invoiced": 0, "paid": 0, "rejected": 0,
        "total_earned": 0.0,
    }
    for status, count, earned in rows:
        stats[status] = count
        if earned:
            stats["total_earned"] += earned

    return stats
