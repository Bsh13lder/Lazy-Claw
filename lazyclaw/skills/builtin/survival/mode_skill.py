"""Survival mode toggle and status display."""

from __future__ import annotations

import json
import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SurvivalModeSkill(BaseSkill):
    """Enable or disable automatic job hunting."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "survival_mode"

    @property
    def description(self) -> str:
        return (
            "Enable or disable survival mode. When ON, LazyClaw automatically "
            "searches for matching jobs every 30 minutes, monitors active gigs, "
            "and notifies you on Telegram. "
            "Usage: 'enable survival mode' or 'turn off job hunting'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true to enable, false to disable",
                },
            },
            "required": ["enabled"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.orchestrator import create_job
        from lazyclaw.survival.profile import get_profile

        enabled = params.get("enabled", False)
        profile = await get_profile(self._config, user_id)

        if enabled and not profile.skills:
            return (
                "Let's set up LazyClaw AI Agent first!\n\n"
                "Tell me:\n"
                "1. Your skills (e.g., 'my skills are python, fastapi, react')\n"
                "2. Your minimum rate (e.g., 'minimum $40/hour')\n"
                "3. Which platforms (e.g., 'search on upwork, indeed')\n\n"
                "LazyClaw will apply as an AI agent — clients know they're hiring AI.\n"
                "You (the founder) approve all critical actions: apply, submit, invoice.\n\n"
                "Set your profile, then enable survival mode."
            )

        if enabled:
            await self._remove_survival_jobs(user_id)

            keywords = " ".join(profile.skills[:5])
            branding = "LazyClaw AI Agent" if profile.branding_mode == "lazyclaw" else "Personal"

            # Job search cron (every 30 min)
            await create_job(
                self._config, user_id,
                name="survival_job_search",
                instruction=(
                    f"Search for freelance jobs matching my profile: {keywords}. "
                    "Only show 70%+ matches. Send results to Telegram."
                ),
                job_type="cron",
                cron_expression="*/30 * * * *",
                context=json.dumps({"survival_mode": True}),
            )

            # Message checker cron (every 15 min)
            await create_job(
                self._config, user_id,
                name="survival_message_check",
                instruction=(
                    "Check for new messages from clients on Upwork. "
                    "Notify me on Telegram if any need response."
                ),
                job_type="cron",
                cron_expression="*/15 * * * *",
                context=json.dumps({"survival_mode": True}),
            )

            # Gig monitor cron (every hour)
            await create_job(
                self._config, user_id,
                name="survival_gig_monitor",
                instruction=(
                    "Check status of active gigs: client messages, deadline warnings, "
                    "payment status. Notify on Telegram if action needed."
                ),
                job_type="cron",
                cron_expression="0 * * * *",
                context=json.dumps({"survival_mode": True}),
            )

            return (
                f"Survival Mode: ON\n"
                f"Identity: {branding}\n\n"
                f"Skills: {', '.join(profile.skills)}\n"
                f"Min rate: ${profile.min_hourly_rate}/hr\n"
                f"Platforms: {', '.join(profile.platforms) or 'all'}\n"
                f"Max concurrent jobs: {profile.max_concurrent_jobs}\n\n"
                f"Job search: every 30 min\n"
                f"Message check: every 15 min\n"
                f"Gig monitor: every hour\n\n"
                f"I'll notify you on Telegram when I find matches.\n"
                f"You approve every application and submission."
            )

        removed = await self._remove_survival_jobs(user_id)
        if removed:
            return "Survival mode OFF. Job hunting paused."
        return "Survival mode was already OFF."

    async def _remove_survival_jobs(self, user_id: str) -> int:
        from lazyclaw.heartbeat.orchestrator import delete_job, list_jobs

        jobs = await list_jobs(self._config, user_id)
        removed = 0
        for job in jobs:
            name = job.get("name", "")
            if name.startswith("survival_"):
                await delete_job(self._config, user_id, job["id"])
                removed += 1
        return removed


class SurvivalStatusSkill(BaseSkill):
    """Show survival mode status and stats. No LLM call — instant."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "survival_status"

    @property
    def description(self) -> str:
        return (
            "Show survival mode status: active gigs, pipeline, earnings. "
            "Usage: 'survival status' or 'how much did I earn'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.orchestrator import list_jobs
        from lazyclaw.survival.gig import get_gig_stats, list_gigs
        from lazyclaw.survival.profile import get_profile

        profile = await get_profile(self._config, user_id)

        # Check if survival crons are active
        jobs = await list_jobs(self._config, user_id)
        survival_crons = [
            j for j in jobs
            if j.get("name", "").startswith("survival_")
            and j.get("status") == "active"
        ]
        is_active = len(survival_crons) > 0

        # Load gig stats from DB
        stats = await get_gig_stats(self._config, user_id)

        branding = "LazyClaw AI Agent" if profile.branding_mode == "lazyclaw" else "Personal"
        status_icon = "ON" if is_active else "OFF"
        lines = [f"Survival Mode: {status_icon} | Identity: {branding}\n"]

        if profile.skills:
            lines.append(f"Skills: {', '.join(profile.skills[:5])}")
            lines.append(f"Min rate: ${profile.min_hourly_rate}/hr")

        lines.append("\nPipeline:")
        lines.append(f"  Applied: {stats.get('applied', 0)}")
        lines.append(f"  Hired: {stats.get('hired', 0)}")
        lines.append(f"  Working: {stats.get('working', 0)}")
        lines.append(f"  Review: {stats.get('review', 0)}")
        lines.append(f"  Delivered: {stats.get('delivered', 0)}")
        lines.append(f"  Invoiced: {stats.get('invoiced', 0)}")
        lines.append(f"  Paid: {stats.get('paid', 0)}")
        lines.append(f"\nTotal Earned: ${stats.get('total_earned', 0):.2f}")

        # Show active gigs
        active_gigs = await list_gigs(
            self._config, user_id, limit=10,
        )
        active = [g for g in active_gigs if g.status in (
            "applied", "hired", "working", "review", "delivered", "invoiced",
        )]

        if active:
            lines.append("\nActive Gigs:")
            for i, gig in enumerate(active[:5], 1):
                lines.append(
                    f"  {i}. \"{gig.title}\" ({gig.platform}) "
                    f"— {gig.status.upper()} — {gig.budget}"
                )

        return "\n".join(lines)
