"""Rollup cascade — heartbeat crons that fold older notes into rollup pages.

Two tiers:

- **Weekly** (Sunday 22:00 user-local): notes from the past 7 days get
  summarised into one ``#rollup/weekly`` note. The agent picks a 3-7 word
  topic phrase as the rollup's title (NOT "Weekly rollup — W23", that's
  boring) and tags it with the ISO week so the cascade can find it later.
  After save, the agent calls :class:`MarkRolledUpSkill` to tag every
  source note with ``#rolled-up`` and ``#source-of/<week>`` so the default
  sidebar/graph view hides the originals (they remain in the DB and remain
  searchable when the agent passes ``include_rolled_up=True``).

- **Monthly** (1st of month, 22:00): ``#rollup/weekly`` notes from the past
  31 days get folded into one ``#rollup/monthly`` note with the same
  title-as-topic + source-marking pattern. Net effect: day 1–7 individual,
  day 8 inside a weekly, day 31 inside a monthly.

Both jobs are opt-in (LLM credits cost the user, consent belongs to them).
The web UI's Settings → Compaction panel reads the same registration to
expose adjustable lookback days.
"""
from __future__ import annotations

import logging

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.heartbeat.orchestrator import create_job

logger = logging.getLogger(__name__)

# Weekly tier ─────────────────────────────────────────────────────────────
_WEEKLY_JOB_TYPE = "lazybrain_rollup"  # legacy name kept for back-compat
_DEFAULT_WEEKLY_CRON = "0 22 * * 0"  # Sunday 22:00 user-local
_DEFAULT_WEEKLY_LOOKBACK_DAYS = 7

_WEEKLY_INSTRUCTION = (
    "Run the weekly LazyBrain rollup for the past {lookback_days} days.\n"
    "\n"
    "Steps (do them in this order):\n"
    "1. Compute the current ISO week label, e.g. `W23-2026`.\n"
    "2. Use `lazybrain_search_notes` (with `include_rolled_up=False`, the "
    "default) and `lazybrain_list_pinned` to gather every note created in "
    "the past {lookback_days} days that does NOT already carry "
    "`#rollup` or `#rolled-up` tags. Collect their IDs.\n"
    "3. Pick a 3-7 word **topic phrase** that captures the dominant theme "
    "of the week. Concrete and human, not a date. Examples: "
    "'Survival pipeline + UI overhaul', 'Migrating Google integrations', "
    "'Finishing the rollup feature'.\n"
    "4. Create the rollup with `lazybrain_save_note`:\n"
    "   - `title`: the topic phrase from step 3 (NOT 'Weekly rollup — W23').\n"
    "   - `tags`: ['rollup', 'kind/rollup', 'rollup/weekly', "
    "'rollup/weekly/W23-2026', 'week/W23-2026', 'source-count/N'] where "
    "N is the source count.\n"
    "   - `importance`: 7.\n"
    "   - `content`: a short summary plus a list of `[[source title]]` "
    "wikilinks to every source so the graph stays connected.\n"
    "5. After save, call `lazybrain_mark_rolled_up` ONCE with the list of "
    "source IDs and `period_id='W23-2026'` and `rollup_id=<the rollup's id>`. "
    "This tags each source with `#rolled-up` and `#source-of/W23-2026` so "
    "the default sidebar and graph hide them (originals stay searchable "
    "via `lazybrain_search_notes(include_rolled_up=True)`).\n"
    "6. Stop. Do not re-run, do not create extra notes. Idempotent: if a "
    "rollup for this ISO week already exists, just exit.\n"
)

# Monthly tier ────────────────────────────────────────────────────────────
_MONTHLY_JOB_TYPE = "lazybrain_rollup_monthly"
_DEFAULT_MONTHLY_CRON = "0 22 1 * *"  # 1st of month, 22:00 user-local
_DEFAULT_MONTHLY_LOOKBACK_DAYS = 31

_MONTHLY_INSTRUCTION = (
    "Run the monthly LazyBrain cascade for the past {lookback_days} days.\n"
    "\n"
    "Steps:\n"
    "1. Compute the current ISO month label, e.g. `M2026-05`.\n"
    "2. Use `lazybrain_search_notes(query='rollup/weekly', "
    "include_rolled_up=False)` to gather all weekly rollup notes from the "
    "past {lookback_days} days. Collect their IDs.\n"
    "3. Pick a 3-7 word **topic phrase** that captures the dominant theme "
    "of the month across those weeklies.\n"
    "4. Create the monthly rollup with `lazybrain_save_note`:\n"
    "   - `title`: the topic phrase.\n"
    "   - `tags`: ['rollup', 'kind/rollup', 'rollup/monthly', "
    "'rollup/monthly/M2026-05', 'month/M2026-05', 'source-count/N'].\n"
    "   - `importance`: 8.\n"
    "   - `content`: short summary plus `[[weekly title]]` wikilinks to "
    "every weekly rollup folded in.\n"
    "5. Call `lazybrain_mark_rolled_up` with the weekly IDs and "
    "`period_id='M2026-05'` and `rollup_id=<monthly rollup id>`. Weeklies "
    "now hide from the default view; only the monthly remains visible.\n"
    "6. Stop. Idempotent — if a monthly rollup already exists for this "
    "month, just exit.\n"
)


# ── Weekly registration ───────────────────────────────────────────────────


async def has_weekly_rollup(config: Config, user_id: str) -> bool:
    """Return True if the user already has a weekly rollup cron registered."""
    return await _has_job(config, user_id, _WEEKLY_JOB_TYPE)


async def ensure_weekly_rollup(
    config: Config,
    user_id: str,
    *,
    cron_expression: str = _DEFAULT_WEEKLY_CRON,
    lookback_days: int = _DEFAULT_WEEKLY_LOOKBACK_DAYS,
) -> str | None:
    """Idempotent: register the weekly rollup cron if not already registered.

    ``lookback_days`` is folded into the agent instruction at registration
    time, so changing the value re-renders the prompt for the *next* job
    creation. Existing jobs keep their original prompt until re-registered.
    """
    if await has_weekly_rollup(config, user_id):
        return None
    instruction = _WEEKLY_INSTRUCTION.format(lookback_days=lookback_days)
    try:
        return await create_job(
            config,
            user_id,
            name="LazyBrain weekly rollup",
            instruction=instruction,
            job_type=_WEEKLY_JOB_TYPE,
            cron_expression=cron_expression,
        )
    except Exception:
        logger.debug("ensure_weekly_rollup failed", exc_info=True)
        return None


# ── Monthly registration ──────────────────────────────────────────────────


async def has_monthly_rollup(config: Config, user_id: str) -> bool:
    """Return True if the user already has a monthly rollup cron registered."""
    return await _has_job(config, user_id, _MONTHLY_JOB_TYPE)


async def ensure_monthly_rollup(
    config: Config,
    user_id: str,
    *,
    cron_expression: str = _DEFAULT_MONTHLY_CRON,
    lookback_days: int = _DEFAULT_MONTHLY_LOOKBACK_DAYS,
) -> str | None:
    """Idempotent: register the monthly cascade cron if not already registered."""
    if await has_monthly_rollup(config, user_id):
        return None
    instruction = _MONTHLY_INSTRUCTION.format(lookback_days=lookback_days)
    try:
        return await create_job(
            config,
            user_id,
            name="LazyBrain monthly rollup",
            instruction=instruction,
            job_type=_MONTHLY_JOB_TYPE,
            cron_expression=cron_expression,
        )
    except Exception:
        logger.debug("ensure_monthly_rollup failed", exc_info=True)
        return None


# ── Internal helpers ──────────────────────────────────────────────────────


async def _has_job(config: Config, user_id: str, job_type: str) -> bool:
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT 1 FROM agent_jobs "
            "WHERE user_id = ? AND job_type = ? AND status = 'active' LIMIT 1",
            (user_id, job_type),
        )
        row = await rows.fetchone()
    return row is not None
