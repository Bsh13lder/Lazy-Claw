"""switch_browser_account — pin a domain to a registered account slug."""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class SwitchBrowserAccountSkill(BaseSkill):
    """Set which registered account is active on a given domain.

    Subsequent browser actions on that domain will route through the
    account's isolated Chromium profile (cookies + storage) — so e.g.
    "post on Reddit as Hirossa main" no longer collides with the side-
    hustle account's session.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "switch_browser_account"

    @property
    def display_name(self) -> str:
        return "Switch Browser Account"

    @property
    def description(self) -> str:
        return (
            "Pin a registered browser account as the active identity for a "
            "domain. Pass `slug='-'` to unpin (revert to the default profile)."
        )

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Site to set the active account on (e.g. 'reddit.com').",
                },
                "slug": {
                    "type": "string",
                    "description": (
                        "Slug of a registered account, or '-' to clear the "
                        "active pointer for this domain."
                    ),
                },
            },
            "required": ["domain", "slug"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        domain = (params.get("domain") or "").strip().lower()
        slug_raw = (params.get("slug") or "").strip().lower()
        if not domain or not slug_raw:
            return "Missing required field: domain or slug."

        from lazyclaw.browser.browser_settings import (
            set_active_account, get_account, normalize_domain,
        )

        if slug_raw in {"-", "none", "default", "off"}:
            await set_active_account(self._config, user_id, domain, None)
            return (
                f"Cleared active account for `{normalize_domain(domain)}` — "
                "browser actions on this domain will use the default profile."
            )

        account = await get_account(self._config, user_id, slug_raw)
        if account is None:
            return (
                f"No account `{slug_raw}` registered. Run "
                "`register_browser_account` first."
            )

        try:
            await set_active_account(self._config, user_id, domain, account.slug)
        except ValueError as exc:
            return f"Cannot switch: {exc}"

        return (
            f"`{normalize_domain(domain)}` is now active as "
            f"**{account.friendly_name}** (`{account.slug}`). "
            "Next browser action on that domain will use this profile."
        )
