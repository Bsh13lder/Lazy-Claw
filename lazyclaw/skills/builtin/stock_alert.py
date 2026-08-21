"""Skill: schedule a recurring low-stock alert.

A business-focused wrapper over the cron/heartbeat scheduler. The agent
describes how to read stock (a recorded `panel_call` endpoint or a read-only
db-toolbox query) and what "low" means; this schedules a recurring check that
alerts the user only when something is low. Restock is optional and always
gated on user approval.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class ScheduleStockAlertSkill(BaseSkill):
    """Set up a recurring low-stock monitor with notification."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "schedule_stock_alert"

    @property
    def description(self) -> str:
        return (
            "Schedule a recurring low-stock check that alerts the user when "
            "inventory runs low. Reads current stock via a recorded panel_call "
            "endpoint or a read-only db-toolbox query, compares to a threshold, "
            "and notifies only when something is low. Alert-only unless a gated "
            "restock path is given. Provide a cron schedule (e.g. '0 9 * * *')."
        )

    @property
    def category(self) -> str:
        return "utility"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short name, e.g. 'Shop low-stock'"},
                "cron_expression": {
                    "type": "string",
                    "description": "5-field cron: minute hour day month weekday. e.g. '0 9 * * *' daily 9am",
                },
                "how_to_check": {
                    "type": "string",
                    "description": (
                        "Exact read to run each time, e.g. \"call panel_call with "
                        "site='shop', name='low_stock'\" or \"run the db-toolbox "
                        "'low_stock' query\"."
                    ),
                },
                "low_condition": {
                    "type": "string",
                    "description": "What counts as low, e.g. 'quantity below 5'",
                },
                "restock": {
                    "type": "string",
                    "description": (
                        "Optional. How to reorder (a panel_call endpoint). Used ONLY "
                        "with the user's explicit approval; omit for alert-only."
                    ),
                },
            },
            "required": ["name", "cron_expression", "how_to_check", "low_condition"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.cron import is_valid
        from lazyclaw.tasks.stock_watch import create_stock_watch_job

        name = (params.get("name") or "").strip()
        cron_expr = (params.get("cron_expression") or "").strip()
        how_to_check = (params.get("how_to_check") or "").strip()
        low_condition = (params.get("low_condition") or "").strip()
        restock = (params.get("restock") or "").strip() or None

        if not (name and cron_expr and how_to_check and low_condition):
            return (
                "Missing required fields: name, cron_expression, how_to_check, "
                "and low_condition."
            )
        if not is_valid(cron_expr):
            return (
                f"Invalid cron expression: '{cron_expr}'. Use 5 fields: "
                "minute hour day month weekday, e.g. '0 9 * * *' for daily at 9am."
            )

        try:
            job_id = await create_stock_watch_job(
                self._config,
                user_id,
                name=name,
                cron_expression=cron_expr,
                how_to_check=how_to_check,
                low_condition=low_condition,
                restock=restock,
            )
        except Exception as exc:
            logger.error("Failed to schedule stock alert: %s", exc, exc_info=True)
            return f"Failed to schedule stock alert: {exc}"

        mode = "alert + gated restock" if restock else "alert-only"
        return (
            f"Low-stock alert '{name}' scheduled ({mode}).\n"
            f"Schedule: {cron_expr}\n"
            f"Checks: {how_to_check}\n"
            f"Low when: {low_condition}\n"
            f"ID: {job_id}"
        )
