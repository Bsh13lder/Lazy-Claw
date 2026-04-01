"""Job management: CRUD for agent_jobs table with encrypted fields."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import decrypt, encrypt, is_encrypted
from lazyclaw.db.connection import db_session
from lazyclaw.heartbeat.cron import calculate_next_run

logger = logging.getLogger(__name__)

ENCRYPTED_FIELDS = ("name", "instruction", "context")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encrypt_field(value: str | None, key: bytes) -> str | None:
    if value is None:
        return None
    return encrypt(value, key)


def _decrypt_field(value: str | None, key: bytes) -> str | None:
    if value is None:
        return None
    return decrypt(value, key) if is_encrypted(value) else value


def _row_to_dict(row, columns: list[str], key: bytes) -> dict:
    result = {}
    for i, col in enumerate(columns):
        value = row[i]
        if col in ENCRYPTED_FIELDS:
            value = _decrypt_field(value, key)
        result[col] = value
    return result


JOB_COLUMNS = [
    "id", "user_id", "name", "job_type", "instruction",
    "cron_expression", "context", "status", "last_run",
    "next_run", "created_at",
]

JOB_SELECT = ", ".join(JOB_COLUMNS)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_job(
    config: Config,
    user_id: str,
    name: str,
    instruction: str,
    job_type: str = "cron",
    cron_expression: str | None = None,
    context: str | None = None,
) -> str:
    """Create a new agent job. Returns the job ID."""
    key = await get_user_dek(config, user_id)
    job_id = str(uuid4())

    encrypted_name = encrypt(name, key)
    encrypted_instruction = encrypt(instruction, key)
    encrypted_context = _encrypt_field(context, key)

    next_run = calculate_next_run(cron_expression) if cron_expression else None

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO agent_jobs "
            "(id, user_id, name, job_type, instruction, cron_expression, "
            "context, status, next_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
            (
                job_id, user_id, encrypted_name, job_type,
                encrypted_instruction, cron_expression,
                encrypted_context, next_run,
            ),
        )
        await db.commit()

    logger.debug("Created job %s for user %s", job_id, user_id)
    return job_id


async def update_job(
    config: Config,
    user_id: str,
    job_id: str,
    **fields,
) -> bool:
    """Update job fields. Encrypts sensitive fields automatically."""
    if not fields:
        return False

    key = await get_user_dek(config, user_id)
    set_clauses: list[str] = []
    params: list = []

    for field_name, value in fields.items():
        if field_name in ENCRYPTED_FIELDS and value is not None:
            value = encrypt(value, key)
        set_clauses.append(f"{field_name} = ?")
        params.append(value)

    if "cron_expression" in fields and fields["cron_expression"] is not None:
        next_run = calculate_next_run(fields["cron_expression"])
        set_clauses.append("next_run = ?")
        params.append(next_run)

    params.extend([job_id, user_id])

    async with db_session(config) as db:
        result = await db.execute(
            f"UPDATE agent_jobs SET {', '.join(set_clauses)} "
            "WHERE id = ? AND user_id = ?",
            params,
        )
        await db.commit()
        return result.rowcount > 0


async def delete_job(config: Config, user_id: str, job_id: str) -> bool:
    """Delete a job. Returns True if a row was deleted."""
    async with db_session(config) as db:
        result = await db.execute(
            "DELETE FROM agent_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id),
        )
        await db.commit()
        return result.rowcount > 0


async def list_jobs(config: Config, user_id: str) -> list[dict]:
    """List all jobs for a user, decrypted."""
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {JOB_SELECT} FROM agent_jobs WHERE user_id = ? "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()

    return [_row_to_dict(row, JOB_COLUMNS, key) for row in rows]


async def get_job(config: Config, user_id: str, job_id: str) -> dict | None:
    """Get a single job by ID, decrypted."""
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        cursor = await db.execute(
            f"SELECT {JOB_SELECT} FROM agent_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id),
        )
        row = await cursor.fetchone()

    if row is None:
        return None
    return _row_to_dict(row, JOB_COLUMNS, key)


async def pause_job(config: Config, user_id: str, job_id: str) -> bool:
    """Pause an active job."""
    async with db_session(config) as db:
        result = await db.execute(
            "UPDATE agent_jobs SET status = 'paused' "
            "WHERE id = ? AND user_id = ? AND status = 'active'",
            (job_id, user_id),
        )
        await db.commit()
        return result.rowcount > 0


async def resume_job(config: Config, user_id: str, job_id: str) -> bool:
    """Resume a paused job. Recalculates next_run."""
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT cron_expression FROM agent_jobs "
            "WHERE id = ? AND user_id = ? AND status = 'paused'",
            (job_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return False

        cron_expression = row[0]
        next_run = calculate_next_run(cron_expression) if cron_expression else None

        await db.execute(
            "UPDATE agent_jobs SET status = 'active', next_run = ? "
            "WHERE id = ? AND user_id = ?",
            (next_run, job_id, user_id),
        )
        await db.commit()
        return True


async def mark_run(config: Config, job_id: str, next_run: str | None) -> None:
    """Update last_run to now and set the next_run value."""
    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "UPDATE agent_jobs SET last_run = ?, next_run = ? WHERE id = ?",
            (now, next_run, job_id),
        )
        await db.commit()
