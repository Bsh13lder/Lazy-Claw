"""Weekly rollup — heartbeat cron that tells the agent to summarise notes
created in the past week into a single ``#rollup/weekly/<iso-week>`` note.

Opt-in per user via :func:`ensure_weekly_rollup` (invoked by the
``lazybrain_enable_weekly_rollup`` skill). We deliberately don't auto-provision
at startup — the rollup runs the agent with LLM credits, and consent belongs to
the user, not the installer.
"""
from __future__ import annotations

import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.heartbeat.orchestrator import create_job

logger = logging.getLogger(__name__)

_ROLLUP_JOB_TYPE = "lazybrain_rollup"
_DEFAULT_CRON = "0 22 * * 0"  # Sunday 22:00 user-local

_ROLLUP_INSTRUCTION = (
    "Summarise every LazyBrain note I saved in the past 7 days into one "
    "rollup note. Use `lazybrain_search_notes` with tag filters and recent "
    "`lazybrain_list_pinned` output to find sources. Create the rollup with "
    "`lazybrain_save_note`, titled 'Weekly rollup — <ISO week>', tagged "
    "`#rollup #rollup/weekly`, importance 7. Reference the source notes via "
    "[[wikilinks]] so the graph grows."
)


async def has_weekly_rollup(config: Config, user_id: str) -> bool:
    """Return True if the user already has a rollup cron job registered."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT 1 FROM agent_jobs "
            "WHERE user_id = ? AND job_type = ? AND status = 'active' LIMIT 1",
            (user_id, _ROLLUP_JOB_TYPE),
        )
        row = await rows.fetchone()
    return row is not None


async def ensure_weekly_rollup(
    config: Config,
    user_id: str,
    *,
    cron_expression: str = _DEFAULT_CRON,
) -> str | None:
    """Idempotent: register the rollup cron if it isn't already registered."""
    if await has_weekly_rollup(config, user_id):
        return None
    try:
        return await create_job(
            config,
            user_id,
            name="LazyBrain weekly rollup",
            instruction=_ROLLUP_INSTRUCTION,
            job_type=_ROLLUP_JOB_TYPE,
            cron_expression=cron_expression,
        )
    except Exception:
        logger.debug("ensure_weekly_rollup failed", exc_info=True)
        return None
