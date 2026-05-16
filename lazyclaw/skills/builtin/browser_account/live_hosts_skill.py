"""Manage the user's extra live-browser watcher hosts.

Watchers polling Cloudflare-protected sites (upwork.com, linkedin.com)
must run through the user's signed-in Brave on the primary CDP port —
a fresh headless instance with a copied profile fails the JS/TLS
fingerprint challenge and silently returns empty extractor output
forever. lazyclaw ships a builtin set of such hosts in
``lazyclaw/heartbeat/daemon.py::_LIVE_BROWSER_WATCHER_HOSTS_BUILTIN``;
these three skills let the user (or the contract-intake auto-setup
flow) extend the list at runtime without a code deploy.

  - :class:`AddLiveBrowserHostSkill` — append a host
  - :class:`RemoveLiveBrowserHostSkill` — drop a user-added host
  - :class:`ListLiveBrowserHostsSkill` — show builtin + user-added

Builtins can NEVER be removed from here (the daemon's frozenset is the
authority). The user list is stored in
``users.settings.browser.live_hosts`` (JSON array of normalized domain
strings — no protocol, no path).
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class AddLiveBrowserHostSkill(BaseSkill):
    """Add a host to the live-browser watcher list."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "add_live_browser_host"

    @property
    def display_name(self) -> str:
        return "Add Live-Browser Watcher Host"

    @property
    def description(self) -> str:
        return (
            "Add a domain to the live-browser watcher list so watchers on "
            "that site poll through your signed-in Brave (not the headless "
            "fallback that gets Cloudflare-blocked). Use when you set up a "
            "watcher on a new gig platform (TaskRabbit, Field Nation, etc.) "
            "and notifications never fire even though items appear on the "
            "page. Subdomain match is automatic — adding 'taskrabbit.com' "
            "also covers 'www.taskrabbit.com'."
        )

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": (
                        "Domain to add (e.g. 'taskrabbit.com'). "
                        "Protocol / path / port stripped automatically; "
                        "leading 'www.' removed."
                    ),
                },
            },
            "required": ["host"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        host = (params.get("host") or "").strip()
        if not host:
            return "host is required"
        from lazyclaw.browser.browser_settings import add_live_host
        try:
            hosts = await add_live_host(self._config, user_id, host)
        except ValueError as exc:
            return f"Cannot add host: {exc}"
        return (
            f"Added live-browser watcher host. User list ({len(hosts)}): "
            f"{', '.join(hosts) or '(empty)'}\n"
            "Builtin set (always-on): upwork.com, linkedin.com"
        )


class RemoveLiveBrowserHostSkill(BaseSkill):
    """Remove a user-added host from the live-browser watcher list."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "remove_live_browser_host"

    @property
    def display_name(self) -> str:
        return "Remove Live-Browser Watcher Host"

    @property
    def description(self) -> str:
        return (
            "Remove a domain from the live-browser watcher list. Builtin "
            "hosts (upwork.com, linkedin.com) CANNOT be removed from here "
            "— they're enforced in the daemon. Only user-added hosts are "
            "removable."
        )

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Domain to remove (e.g. 'taskrabbit.com').",
                },
            },
            "required": ["host"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        host = (params.get("host") or "").strip()
        if not host:
            return "host is required"
        from lazyclaw.browser.browser_settings import remove_live_host
        try:
            hosts = await remove_live_host(self._config, user_id, host)
        except ValueError as exc:
            return f"Cannot remove host: {exc}"
        return (
            f"Removed live-browser watcher host (if present). User list "
            f"({len(hosts)}): {', '.join(hosts) or '(empty)'}\n"
            "Builtin set (still active): upwork.com, linkedin.com"
        )


class ListLiveBrowserHostsSkill(BaseSkill):
    """Show all hosts the watcher framework will route through live Brave."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_live_browser_hosts"

    @property
    def display_name(self) -> str:
        return "List Live-Browser Watcher Hosts"

    @property
    def description(self) -> str:
        return (
            "Show every host the watcher framework polls through your "
            "signed-in Brave (instead of the headless fallback). Two "
            "sections: builtin (shipped with lazyclaw) and user-added."
        )

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser.browser_settings import get_live_hosts
        from lazyclaw.heartbeat.daemon import (
            _LIVE_BROWSER_WATCHER_HOSTS_BUILTIN,
        )
        user_hosts = await get_live_hosts(self._config, user_id)
        builtin = sorted(_LIVE_BROWSER_WATCHER_HOSTS_BUILTIN)
        lines = [
            f"Builtin ({len(builtin)}): {', '.join(builtin)}",
            f"User-added ({len(user_hosts)}): {', '.join(user_hosts) or '(none)'}",
        ]
        return "\n".join(lines)
