"""Rollup admin skill — opt-in weekly rollup cron."""
from __future__ import annotations

from lazyclaw.lazybrain import rollup as rollup_mod
from lazyclaw.skills.base import BaseSkill


class EnableWeeklyRollupSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_enable_weekly_rollup"

    @property
    def display_name(self) -> str:
        return "Enable weekly rollup"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Register a weekly heartbeat cron that tells the agent to "
            "summarise new notes into a [[rollup]] page every Sunday night. "
            "Idempotent — no-op if already registered."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "cron": {
                    "type": "string",
                    "description": "Override schedule (default: Sunday 22:00 — '0 22 * * 0').",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        cron = params.get("cron") or "0 22 * * 0"
        existing = await rollup_mod.has_weekly_rollup(self._config, user_id)
        if existing:
            return "Weekly rollup is already enabled."
        job_id = await rollup_mod.ensure_weekly_rollup(
            self._config, user_id, cron_expression=cron
        )
        if not job_id:
            return "❌ Could not register the rollup cron (see logs)."
        return f"✅ Weekly rollup enabled (cron {cron}). Job id: {job_id[:8]}."
