"""Encrypted CRUD for bounty_programs / bounty_findings / bounty_audit.

All sensitive fields (scope_assets, finding bodies, target URLs) round-trip
through the existing user-DEK envelope (`enc:v1:<nonce>:<ct>`). Status,
severity, platform, vuln_class stay plaintext so the agent can filter and
the audit log stays SQL-queryable.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


# ── AAD helpers ─────────────────────────────────────────────────────────────
# AAD binds each ciphertext to (user, logical-field) so a swap attempt across
# users or fields fails authentication. Mirrors the lazybrain.store pattern.

def _scope_aad(user_id: str) -> bytes:
    return f"bounty:scope:{user_id}".encode("utf-8")


def _excluded_aad(user_id: str) -> bytes:
    return f"bounty:excluded:{user_id}".encode("utf-8")


def _classes_aad(user_id: str) -> bytes:
    return f"bounty:classes:{user_id}".encode("utf-8")


def _finding_title_aad(user_id: str) -> bytes:
    return f"bounty:finding:title:{user_id}".encode("utf-8")


def _finding_poc_aad(user_id: str) -> bytes:
    return f"bounty:finding:poc:{user_id}".encode("utf-8")


def _finding_url_aad(user_id: str) -> bytes:
    return f"bounty:finding:url:{user_id}".encode("utf-8")


def _audit_target_aad(user_id: str) -> bytes:
    return f"bounty:audit:target:{user_id}".encode("utf-8")


# ── Programs ────────────────────────────────────────────────────────────────


async def register_program(
    config: Config,
    user_id: str,
    *,
    name: str,
    platform: str,
    scope_assets: list[str],
    excluded_assets: list[str] | None = None,
    excluded_classes: list[str] | None = None,
    rate_limit_rps: int = 5,
) -> dict[str, Any]:
    """Register a new bounty program. Idempotent on (user_id, name)."""
    if platform not in {"intigriti", "yeswehack", "hackerone", "bugcrowd"}:
        raise ValueError(f"unsupported platform: {platform}")
    if not scope_assets:
        raise ValueError("scope_assets must not be empty — refuse to scan blind")
    if rate_limit_rps < 1 or rate_limit_rps > 50:
        raise ValueError("rate_limit_rps must be between 1 and 50")

    dek = await get_user_dek(config, user_id)
    program_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    enc_scope = encrypt_field(json.dumps(scope_assets), dek, _scope_aad(user_id))
    enc_excluded = (
        encrypt_field(json.dumps(excluded_assets or []), dek, _excluded_aad(user_id))
        if excluded_assets
        else None
    )
    enc_classes = (
        encrypt_field(json.dumps(excluded_classes or []), dek, _classes_aad(user_id))
        if excluded_classes
        else None
    )

    async with db_session(config) as conn:
        try:
            await conn.execute(
                """
                INSERT INTO bounty_programs
                    (id, user_id, name, platform, scope_assets, excluded_assets,
                     excluded_classes, rate_limit_rps, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (program_id, user_id, name, platform, enc_scope, enc_excluded,
                 enc_classes, rate_limit_rps, now, now),
            )
            await conn.commit()
        except Exception as exc:
            # UNIQUE(user_id, name) violation surfaces as "duplicate" so the
            # caller can react with a clear message.
            if "UNIQUE" in str(exc) or "constraint" in str(exc).lower():
                raise ValueError(f"program '{name}' already exists for this user")
            raise

    return {
        "id": program_id,
        "name": name,
        "platform": platform,
        "scope_assets": scope_assets,
        "excluded_assets": excluded_assets or [],
        "excluded_classes": excluded_classes or [],
        "rate_limit_rps": rate_limit_rps,
        "enabled": True,
    }


async def list_programs(
    config: Config, user_id: str, *, enabled_only: bool = False,
) -> list[dict[str, Any]]:
    """Return decrypted programs for a user."""
    dek = await get_user_dek(config, user_id)

    where = "WHERE user_id = ?"
    args: list[Any] = [user_id]
    if enabled_only:
        where += " AND enabled = 1"

    async with db_session(config) as conn:
        async with conn.execute(
            f"""
            SELECT id, name, platform, scope_assets, excluded_assets,
                   excluded_classes, rate_limit_rps, enabled, created_at
            FROM bounty_programs
            {where}
            ORDER BY created_at DESC
            """,
            args,
        ) as cur:
            rows = await cur.fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        scope = json.loads(decrypt_field(row[3], dek, _scope_aad(user_id)))
        excluded = (
            json.loads(decrypt_field(row[4], dek, _excluded_aad(user_id)))
            if row[4] else []
        )
        classes = (
            json.loads(decrypt_field(row[5], dek, _classes_aad(user_id)))
            if row[5] else []
        )
        out.append({
            "id": row[0],
            "name": row[1],
            "platform": row[2],
            "scope_assets": scope,
            "excluded_assets": excluded,
            "excluded_classes": classes,
            "rate_limit_rps": row[6],
            "enabled": bool(row[7]),
            "created_at": row[8],
        })
    return out


async def get_program(
    config: Config, user_id: str, name: str,
) -> dict[str, Any] | None:
    """Look up one program by name. Returns None if absent."""
    programs = await list_programs(config, user_id)
    return next((p for p in programs if p["name"] == name), None)


async def set_enabled(
    config: Config, user_id: str, name: str, enabled: bool,
) -> bool:
    async with db_session(config) as conn:
        cur = await conn.execute(
            "UPDATE bounty_programs SET enabled = ?, updated_at = ? "
            "WHERE user_id = ? AND name = ?",
            (1 if enabled else 0, datetime.now(timezone.utc).isoformat(),
             user_id, name),
        )
        await conn.commit()
        return cur.rowcount > 0


# ── Findings ────────────────────────────────────────────────────────────────


async def create_finding(
    config: Config,
    user_id: str,
    *,
    program_id: str,
    title: str,
    vuln_class: str,
    severity: str,
    target_url: str,
    poc: str,
    cvss_vector: str | None = None,
    cvss_score: float | None = None,
) -> str:
    """Store a finding in proposed state. Returns finding_id."""
    if severity not in {"info", "low", "medium", "high", "critical"}:
        raise ValueError(f"invalid severity: {severity}")

    dek = await get_user_dek(config, user_id)
    finding_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    enc_title = encrypt_field(title, dek, _finding_title_aad(user_id))
    enc_url = encrypt_field(target_url, dek, _finding_url_aad(user_id))
    enc_poc = encrypt_field(poc, dek, _finding_poc_aad(user_id))

    async with db_session(config) as conn:
        await conn.execute(
            """
            INSERT INTO bounty_findings
                (id, program_id, user_id, title, vuln_class, severity,
                 cvss_vector, cvss_score, poc, target_url, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            """,
            (finding_id, program_id, user_id, enc_title, vuln_class, severity,
             cvss_vector, cvss_score, enc_poc, enc_url, now, now),
        )
        await conn.commit()

    return finding_id


async def list_findings(
    config: Config,
    user_id: str,
    *,
    program_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    dek = await get_user_dek(config, user_id)

    clauses = ["user_id = ?"]
    args: list[Any] = [user_id]
    if program_id:
        clauses.append("program_id = ?")
        args.append(program_id)
    if status:
        clauses.append("status = ?")
        args.append(status)
    where = " AND ".join(clauses)

    async with db_session(config) as conn:
        async with conn.execute(
            f"""
            SELECT id, program_id, title, vuln_class, severity, cvss_score,
                   target_url, status, payout_amount, created_at, updated_at
            FROM bounty_findings
            WHERE {where}
            ORDER BY updated_at DESC
            """,
            args,
        ) as cur:
            rows = await cur.fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({
            "id": row[0],
            "program_id": row[1],
            "title": decrypt_field(row[2], dek, _finding_title_aad(user_id)),
            "vuln_class": row[3],
            "severity": row[4],
            "cvss_score": row[5],
            "target_url": decrypt_field(row[6], dek, _finding_url_aad(user_id)),
            "status": row[7],
            "payout_amount": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        })
    return out


async def get_finding(
    config: Config, user_id: str, finding_id: str,
) -> dict[str, Any] | None:
    """Return a single finding fully decrypted (title + URL + POC)."""
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as conn:
        async with conn.execute(
            """
            SELECT id, program_id, title, vuln_class, severity, cvss_vector,
                   cvss_score, poc, target_url, status, payout_amount,
                   created_at, updated_at
            FROM bounty_findings
            WHERE id = ? AND user_id = ?
            """,
            (finding_id, user_id),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "program_id": row[1],
        "title": decrypt_field(row[2], dek, _finding_title_aad(user_id)),
        "vuln_class": row[3],
        "severity": row[4],
        "cvss_vector": row[5],
        "cvss_score": row[6],
        "poc": decrypt_field(row[7], dek, _finding_poc_aad(user_id)),
        "target_url": decrypt_field(row[8], dek, _finding_url_aad(user_id)),
        "status": row[9],
        "payout_amount": row[10],
        "created_at": row[11],
        "updated_at": row[12],
    }


async def update_finding_status(
    config: Config,
    user_id: str,
    finding_id: str,
    status: str,
    *,
    payout_amount: float | None = None,
) -> bool:
    if status not in {"proposed", "validated", "rejected", "submitted", "paid"}:
        raise ValueError(f"invalid status: {status}")
    now = datetime.now(timezone.utc).isoformat()

    async with db_session(config) as conn:
        if payout_amount is not None:
            cur = await conn.execute(
                "UPDATE bounty_findings SET status = ?, payout_amount = ?, "
                "updated_at = ? WHERE id = ? AND user_id = ?",
                (status, payout_amount, now, finding_id, user_id),
            )
        else:
            cur = await conn.execute(
                "UPDATE bounty_findings SET status = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (status, now, finding_id, user_id),
            )
        await conn.commit()
        return cur.rowcount > 0


# ── Audit log ───────────────────────────────────────────────────────────────


async def write_audit(
    config: Config,
    user_id: str,
    *,
    program_id: str,
    target: str,
    tool: str,
    method: str | None = None,
    decision: str | None = None,
    response_code: int | None = None,
) -> None:
    """Append-only audit row. Best-effort: a logging failure must not block
    the caller. Targets are encrypted because they reveal which assets we
    enumerate (scope-leak risk if the DB is compromised)."""
    try:
        dek = await get_user_dek(config, user_id)
        enc_target = encrypt_field(target, dek, _audit_target_aad(user_id))
        async with db_session(config) as conn:
            await conn.execute(
                """
                INSERT INTO bounty_audit
                    (program_id, user_id, target, tool, method, decision,
                     response_code, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (program_id, user_id, enc_target, tool, method, decision,
                 response_code, datetime.now(timezone.utc).isoformat()),
            )
            await conn.commit()
    except Exception:
        logger.warning(
            "bounty_audit write failed for tool=%s target=%s",
            tool, target, exc_info=True,
        )


async def count_audit(
    config: Config, user_id: str, program_id: str,
) -> int:
    async with db_session(config) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM bounty_audit WHERE program_id = ? AND user_id = ?",
            (program_id, user_id),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0
