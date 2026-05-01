"""Configure the lazydoctor weekly maintenance audit.

Pattern mirrors ``runtime/agent_settings.py`` (storage under
``users.settings['lazydoctor']``) + ``survival/profile_skill.py`` (view-or-
update NL surface). Idempotent — re-running with new args updates the
existing cron job rather than creating a duplicate.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from lazyclaw.db.connection import db_session
from lazyclaw.heartbeat.cron import is_valid as is_valid_cron
from lazyclaw.heartbeat.orchestrator import (
    create_job,
    list_jobs,
    update_job,
)
from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


JOB_NAME = "Lazydoctor weekly audit"

# The instruction the cron daemon enqueues onto the lane queue. The brain
# reads this after the daemon's "[JOB:<name>]" prefix and dispatches to
# the lazydoctor MCP + audit bridge skill. Tagged with [LAZYDOCTOR_AUDIT]
# so it's greppable in logs and never accidentally picked up by other
# routing heuristics.
JOB_INSTRUCTION = (
    "[LAZYDOCTOR_AUDIT] Run the weekly maintenance audit.\n"
    "1. Call doctor_audit (no args, scope='deps').\n"
    "2. For each finding in the returned report, call "
    "lazydoctor_review_finding with its id, title, severity, "
    "proposed_action, and diff_or_command. The bridge handles Telegram "
    "approval and applies the finding asynchronously.\n"
    "3. Push a one-line summary to Telegram: "
    "\"Lazydoctor audit: N findings, X dispatched for approval\".\n"
    "4. Done. Do not chain follow-up actions."
)

DEFAULT_PROFILE: dict[str, Any] = {
    "enabled": True,
    "cadence": "weekly",                # weekly | daily | monthly | custom
    "cron_expression": "0 9 * * 1",     # Mon 09:00 in the daemon's tz
    "severity_min": "medium",           # low | medium | high
    "scope": ["deps"],                   # MVP: deps only
    "approval_mode": "telegram",        # telegram | auto | none
    "dry_run": True,                    # first rollout default
}

_CADENCE_TO_CRON = {
    "weekly": "0 9 * * 1",      # Mon 09:00
    "daily": "0 9 * * *",       # 09:00 every day
    "monthly": "0 9 1 * *",     # 1st of month, 09:00
}

_SEVERITY_VALUES = {"low", "medium", "high"}
_APPROVAL_MODES = {"telegram", "auto", "none"}
_VALID_SCOPES = {"deps", "all"}


# ── Storage helpers ──────────────────────────────────────────────────────

async def get_lazydoctor_profile(config, user_id: str) -> dict:
    try:
        async with db_session(config) as db:
            cursor = await db.execute(
                "SELECT settings FROM users WHERE id = ?", (user_id,),
            )
            row = await cursor.fetchone()
            if row and row[0]:
                blob = json.loads(row[0])
                return {**DEFAULT_PROFILE, **(blob.get("lazydoctor") or {})}
    except Exception as exc:
        logger.debug("get_lazydoctor_profile failed: %s", exc)
    return dict(DEFAULT_PROFILE)


async def update_lazydoctor_profile(
    config, user_id: str, updates: dict,
) -> dict:
    async with db_session(config) as db:
        cursor = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,),
        )
        row = await cursor.fetchone()
        current = json.loads(row[0]) if row and row[0] else {}

        merged = {
            **DEFAULT_PROFILE,
            **(current.get("lazydoctor") or {}),
            **updates,
        }
        new_settings = dict(current)
        new_settings["lazydoctor"] = merged
        await db.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(new_settings), user_id),
        )
        await db.commit()
        return merged


# ── Validation ──────────────────────────────────────────────────────────

def _coerce_updates(raw: dict) -> dict | str:
    """Validate + canonicalize raw NL input. Returns dict on success or a
    user-facing error string on failure."""
    out: dict = {}

    if "enabled" in raw:
        if not isinstance(raw["enabled"], bool):
            return "enabled must be true or false"
        out["enabled"] = raw["enabled"]

    cadence = raw.get("cadence")
    if cadence is not None:
        if cadence not in _CADENCE_TO_CRON and cadence != "custom":
            return (
                f"cadence must be one of weekly/daily/monthly/custom "
                f"(got {cadence!r})"
            )
        out["cadence"] = cadence
        if cadence in _CADENCE_TO_CRON:
            out["cron_expression"] = _CADENCE_TO_CRON[cadence]

    if "cron_expression" in raw and raw["cron_expression"]:
        expr = raw["cron_expression"].strip()
        if not is_valid_cron(expr):
            return f"cron_expression {expr!r} is not a valid 5-field cron"
        out["cron_expression"] = expr
        out.setdefault("cadence", "custom")

    if "severity_min" in raw:
        sev = str(raw["severity_min"]).lower()
        if sev not in _SEVERITY_VALUES:
            return f"severity_min must be one of {sorted(_SEVERITY_VALUES)}"
        out["severity_min"] = sev

    if "scope" in raw:
        scope = raw["scope"]
        if isinstance(scope, str):
            scope = [scope]
        if not isinstance(scope, list) or not scope:
            return "scope must be a non-empty list of strings"
        bad = [s for s in scope if s not in _VALID_SCOPES]
        if bad:
            return (
                f"unknown scope items {bad!r}. MVP supports: "
                f"{sorted(_VALID_SCOPES)}"
            )
        out["scope"] = scope

    if "approval_mode" in raw:
        mode = str(raw["approval_mode"]).lower()
        if mode not in _APPROVAL_MODES:
            return f"approval_mode must be one of {sorted(_APPROVAL_MODES)}"
        out["approval_mode"] = mode

    if "dry_run" in raw:
        if not isinstance(raw["dry_run"], bool):
            return "dry_run must be true or false"
        out["dry_run"] = raw["dry_run"]

    return out


# ── Cron registration ────────────────────────────────────────────────────

async def _ensure_cron_job(config, user_id: str, profile: dict) -> str:
    """Create or update the weekly audit cron job. Idempotent — looks up
    by name. Returns the job_id."""
    jobs = await list_jobs(config, user_id)
    existing = next((j for j in jobs if j.get("name") == JOB_NAME), None)

    if not profile.get("enabled", True):
        # User disabled — pause the job if it exists, else no-op.
        if existing:
            await update_job(
                config, user_id, existing["id"], status="paused",
            )
            return existing["id"]
        return ""

    cron_expr = profile.get("cron_expression") or DEFAULT_PROFILE["cron_expression"]

    if existing is None:
        return await create_job(
            config, user_id, JOB_NAME, JOB_INSTRUCTION,
            job_type="cron", cron_expression=cron_expr,
        )

    await update_job(
        config, user_id, existing["id"],
        instruction=JOB_INSTRUCTION,
        cron_expression=cron_expr,
        status="active",
    )
    return existing["id"]


# ── Skill ────────────────────────────────────────────────────────────────

class LazydoctorSetupSkill(BaseSkill):
    """Configure the lazydoctor weekly maintenance audit."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazydoctor_setup"

    @property
    def description(self) -> str:
        return (
            "Configure the lazydoctor weekly maintenance audit. "
            "No args = view current settings. Update by passing any of: "
            "cadence (weekly|daily|monthly), cron_expression, severity_min "
            "(low|medium|high), scope (deps), approval_mode (telegram|auto|"
            "none), dry_run (bool), enabled (bool). Examples: "
            "'lazydoctor setup' / 'set lazydoctor severity to high' / "
            "'turn off lazydoctor dry-run' / 'disable lazydoctor'."
        )

    @property
    def category(self) -> str:
        # utility = ALLOW (config skill, not a state-mutator).
        return "utility"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "Whether the weekly audit is active.",
                },
                "cadence": {
                    "type": "string",
                    "enum": ["weekly", "daily", "monthly", "custom"],
                    "description": (
                        "How often to run. weekly = Mon 09:00, daily = 09:00, "
                        "monthly = 1st 09:00. Use 'custom' with cron_expression."
                    ),
                },
                "cron_expression": {
                    "type": "string",
                    "description": "5-field cron (e.g. '0 9 * * 1'). Sets cadence='custom' implicitly.",
                },
                "severity_min": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Filter findings below this severity. Default medium.",
                },
                "scope": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["deps", "all"]},
                    "description": "Audit scope. MVP supports deps only.",
                },
                "approval_mode": {
                    "type": "string",
                    "enum": ["telegram", "auto", "none"],
                    "description": "How approvals are gated. MVP: telegram only.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "When True the apply path logs commands without "
                        "executing. Default True for first rollout."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        raw = {
            k: v for k, v in (params or {}).items()
            if v is not None and v != "" and v != []
        }

        if not raw:
            profile = await get_lazydoctor_profile(self._config, user_id)
            jobs = await list_jobs(self._config, user_id)
            existing = next(
                (j for j in jobs if j.get("name") == JOB_NAME), None,
            )
            status = (
                f"{existing.get('status')} (next: {existing.get('next_run')})"
                if existing else "not scheduled"
            )
            return (
                f"Lazydoctor settings:\n"
                f"  enabled: {profile['enabled']}\n"
                f"  cadence: {profile['cadence']} ({profile['cron_expression']})\n"
                f"  severity_min: {profile['severity_min']}\n"
                f"  scope: {', '.join(profile['scope'])}\n"
                f"  approval_mode: {profile['approval_mode']}\n"
                f"  dry_run: {profile['dry_run']}\n"
                f"  cron job: {status}"
            )

        coerced = _coerce_updates(raw)
        if isinstance(coerced, str):
            return coerced  # validation error

        merged = await update_lazydoctor_profile(
            self._config, user_id, coerced,
        )
        try:
            job_id = await _ensure_cron_job(self._config, user_id, merged)
        except Exception as exc:
            logger.exception("Cron registration failed")
            return (
                f"Settings saved but cron registration failed: {exc}. "
                f"Re-run lazydoctor setup to retry."
            )
        changed = ", ".join(f"{k}={v}" for k, v in coerced.items())
        return (
            f"Lazydoctor: {changed}. "
            f"Cron job: {job_id or 'paused'} ({merged['cron_expression']})."
        )
