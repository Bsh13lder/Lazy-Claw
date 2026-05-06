"""Register / disable bounty programs."""
from __future__ import annotations

from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.bounty import store


class BountyRegisterProgramSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "bounty_register_program"

    @property
    def display_name(self) -> str:
        return "Register bounty program"

    @property
    def category(self) -> str:
        return "bounty"

    @property
    def description(self) -> str:
        return (
            "Register a bug-bounty program with explicit scope so the agent "
            "can run guarded recon against it. The scope_assets list is the "
            "ALLOWLIST — anything not matching is refused before any outbound "
            "request. Required ONCE per program before any recon/hunt skill "
            "will operate on it. Platform must be one of: intigriti, "
            "yeswehack, hackerone, bugcrowd."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short program identifier (e.g. 'acronis').",
                },
                "platform": {
                    "type": "string",
                    "enum": ["intigriti", "yeswehack", "hackerone", "bugcrowd"],
                    "description": "Bounty platform hosting this program.",
                },
                "scope_assets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Allowlist of in-scope domain patterns. Use '*.x.com' "
                        "for wildcard subdomains, 'api.x.com' for exact match. "
                        "Copy verbatim from the program's official scope page."
                    ),
                },
                "excluded_assets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional blocklist within the scope (e.g. 'blog.x.com')."
                    ),
                },
                "excluded_classes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Vuln classes the program explicitly forbids "
                        "(e.g. ['dos', 'social_engineering', 'physical'])."
                    ),
                },
                "rate_limit_rps": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Max requests/sec the recon worker will issue (default 5).",
                },
            },
            "required": ["name", "platform", "scope_assets"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        try:
            program = await store.register_program(
                self._config, user_id,
                name=params["name"],
                platform=params["platform"],
                scope_assets=params["scope_assets"],
                excluded_assets=params.get("excluded_assets") or None,
                excluded_classes=params.get("excluded_classes") or None,
                rate_limit_rps=int(params.get("rate_limit_rps") or 5),
            )
        except ValueError as exc:
            return f"❌ Could not register: {exc}"

        scope_summary = ", ".join(program["scope_assets"][:5])
        if len(program["scope_assets"]) > 5:
            scope_summary += f" … (+{len(program['scope_assets']) - 5} more)"
        return (
            f"✅ Registered bounty program: **{program['name']}** "
            f"({program['platform']})\n"
            f"Scope ({len(program['scope_assets'])}): {scope_summary}\n"
            f"Rate limit: {program['rate_limit_rps']} rps\n"
            f"ID: {program['id']}"
        )


class BountyDisableProgramSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "bounty_disable_program"

    @property
    def display_name(self) -> str:
        return "Disable bounty program"

    @property
    def category(self) -> str:
        return "bounty"

    @property
    def description(self) -> str:
        return (
            "Disable a registered bounty program. Recon / hunt skills will "
            "refuse to run against disabled programs. Use this when a program "
            "ends or you stop hunting it — does NOT delete history."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Program name."},
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        ok = await store.set_enabled(self._config, user_id, params["name"], False)
        if not ok:
            return f"❌ No program named '{params['name']}'."
        return f"✅ Disabled bounty program: {params['name']}"
