"""list_browser_accounts — table of registered browser identities."""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class ListBrowserAccountsSkill(BaseSkill):
    """List registered browser accounts. Optionally filter by domain."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_browser_accounts"

    @property
    def display_name(self) -> str:
        return "List Browser Accounts"

    @property
    def description(self) -> str:
        return (
            "List the user's registered browser identities. Pass "
            "`domain` to filter to one site (e.g. only Reddit accounts). "
            "Returns slug, friendly name, primary domain, and which slug "
            "is currently active per domain."
        )

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": (
                        "Optional domain filter (e.g. 'reddit.com')."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        domain_filter = (params.get("domain") or "").strip().lower() or None

        from lazyclaw.browser.browser_settings import (
            list_accounts, get_browser_settings,
        )
        accounts = await list_accounts(self._config, user_id, domain_filter)
        if not accounts:
            return (
                "No browser accounts registered."
                if not domain_filter
                else f"No browser accounts registered for `{domain_filter}`."
            )

        settings = await get_browser_settings(self._config, user_id)
        active_map = settings.get("active_account_by_domain") or {}

        lines = ["**Registered browser accounts**", ""]
        for a in accounts:
            active_marker = (
                " · **active**"
                if active_map.get(a.primary_domain) == a.slug
                else ""
            )
            last_used = a.last_used_at or "—"
            lines.append(
                f"- `{a.slug}` ({a.friendly_name}) → "
                f"`{a.primary_domain}`{active_marker} — last used {last_used}"
            )
        return "\n".join(lines)
