"""On-demand trigger for the lazydoctor audit.

The brain reads this skill's return value and dispatches to the lazydoctor
MCP itself (call doctor_audit, then fan out review_finding per finding).
We don't call the MCP from inside this skill because the lazydoctor MCP
runs in its own process and is reached via the same tool path the brain
uses for any MCP — keeping the lazyclaw side ignorant of MCP transport.
"""
from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class LazydoctorRunNowSkill(BaseSkill):
    """Run an on-demand lazydoctor audit, bypassing the cron schedule."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazydoctor_run_now"

    @property
    def description(self) -> str:
        return (
            "Run the lazydoctor maintenance audit immediately, without "
            "waiting for the weekly cron. Use when the user says "
            "'check for updates now', 'run lazydoctor', 'audit my MCPs'. "
            "Returns instructions for the brain to call the lazydoctor MCP."
        )

    @property
    def category(self) -> str:
        return "utility"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["deps", "all"],
                    "description": "Audit scope. MVP: 'deps' covers everything.",
                },
                "severity_min": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Filter findings below this severity. Default medium.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        scope = (params or {}).get("scope", "deps")
        severity_min = (params or {}).get("severity_min", "medium")
        return (
            f"[LAZYDOCTOR_AUDIT] Run an on-demand audit.\n"
            f"1. Call doctor_audit(scope={scope!r}, severity_min={severity_min!r}).\n"
            f"2. For each finding in the returned report, call "
            f"lazydoctor_review_finding with its id, title, severity, "
            f"proposed_action, and diff_or_command.\n"
            f"3. After all findings are dispatched, push a Telegram "
            f"summary: 'Lazydoctor on-demand audit: N findings, "
            f"X dispatched for approval'.\n"
            f"4. Done. Do not chain further actions."
        )
