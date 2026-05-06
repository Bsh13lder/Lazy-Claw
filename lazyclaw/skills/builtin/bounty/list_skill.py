"""List registered programs and their findings."""
from __future__ import annotations

from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.bounty import store


class BountyListProgramsSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "bounty_list_programs"

    @property
    def display_name(self) -> str:
        return "List bounty programs"

    @property
    def category(self) -> str:
        return "bounty"

    @property
    def description(self) -> str:
        return (
            "List all bounty programs registered for this user. Shows name, "
            "platform, scope size, enabled status, and per-program audit "
            "request count. Use before any recon / hunt to confirm the "
            "program exists locally."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "enabled_only": {
                    "type": "boolean",
                    "description": "Show only enabled programs (default: all).",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        programs = await store.list_programs(
            self._config, user_id,
            enabled_only=bool(params.get("enabled_only")),
        )
        if not programs:
            return (
                "No bounty programs registered. Use bounty_register_program "
                "to add one with explicit scope."
            )

        lines = [f"Bounty programs ({len(programs)}):", ""]
        for p in programs:
            audit_count = await store.count_audit(self._config, user_id, p["id"])
            status = "✅" if p["enabled"] else "⏸"
            scope_count = len(p["scope_assets"])
            lines.append(
                f"{status} **{p['name']}** ({p['platform']}) "
                f"— {scope_count} asset{'s' if scope_count != 1 else ''} in scope, "
                f"{audit_count} recon request{'s' if audit_count != 1 else ''} logged"
            )
        return "\n".join(lines)


class BountyListFindingsSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "bounty_list_findings"

    @property
    def display_name(self) -> str:
        return "List bounty findings"

    @property
    def category(self) -> str:
        return "bounty"

    @property
    def description(self) -> str:
        return (
            "List findings the agent has prepared, optionally filtered by "
            "program or status. Status values: proposed (just found, needs "
            "validation), validated (passed 7-Question Gate), submitted, "
            "paid, rejected. Use before bounty_validate_finding to pick one."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "program_name": {
                    "type": "string",
                    "description": "Filter to one program (optional).",
                },
                "status": {
                    "type": "string",
                    "enum": ["proposed", "validated", "rejected", "submitted", "paid"],
                    "description": "Filter by status (optional).",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        program_id = None
        program_name = params.get("program_name")
        if program_name:
            program = await store.get_program(self._config, user_id, program_name)
            if not program:
                return f"❌ No program named '{program_name}'."
            program_id = program["id"]

        findings = await store.list_findings(
            self._config, user_id,
            program_id=program_id,
            status=params.get("status"),
        )
        if not findings:
            return "No findings match those filters."

        lines = [f"Findings ({len(findings)}):", ""]
        for f in findings:
            sev_emoji = {
                "info": "ℹ️", "low": "🟢", "medium": "🟡",
                "high": "🟠", "critical": "🔴",
            }.get(f["severity"], "·")
            payout = (
                f" — €{f['payout_amount']:.0f}" if f.get("payout_amount") else ""
            )
            lines.append(
                f"{sev_emoji} **{f['title']}** [{f['vuln_class']}] "
                f"`{f['status']}`{payout}\n"
                f"   {f['target_url']}\n"
                f"   ID: `{f['id']}`"
            )
        return "\n".join(lines)
