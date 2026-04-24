"""Host-browser bridge NL skill.

Lets the user say "use my browser", "login as me", "connect to brave",
"stop host browser", etc. from any channel (Telegram, web chat, CLI).

Mirrors the ``share_browser_control`` skill layout so users pick whichever
makes sense:
    - ``share_browser_control``  → VNC takeover; user drives, agent watches
    - ``use_host_browser``       → CDP bridge; agent drives user's real Brave
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class UseHostBrowserSkill(BaseSkill):
    """Toggle the host-browser CDP bridge on / off / check status."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def name(self) -> str:
        return "use_host_browser"

    @property
    def description(self) -> str:
        return (
            "Switch the agent to drive the user's REAL Brave/Chrome on the "
            "host machine (with all their cookies, saved logins, and "
            "extensions) instead of the containerised headless browser. "
            "Call this skill BEFORE the `browser` skill whenever the user "
            "wants to use their own browser identity.\n\n"
            "Trigger phrases (any of these should match — single word, "
            "typo, or other languages too):\n"
            "  - 'use my browser', 'use mybrowser', 'usemybrowser'\n"
            "  - 'use my brave', 'use brave', 'use mybrave', 'my brave'\n"
            "  - 'use my chrome', 'my chrome'\n"
            "  - 'login as me', 'connect to brave', 'use my cookies', "
            "'use my real browser', 'host browser', 'host brave'\n"
            "  - Spanish: 'usa mi navegador', 'mi navegador', 'usa mi brave'\n"
            "  - Georgian/Latin: 'ჩემი ბრაუზერი', 'chemi brauzeri'\n\n"
            "Keywords for search_tools: browser, brave, chrome, host, "
            "cookies, login, real, my, mybrowser, mybrave.\n\n"
            "On first setup, returns a shell one-liner the user has to run "
            "once to relaunch Brave with CDP enabled. 'stop' switches back "
            "to the container browser."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "status"],
                    "description": (
                        "'start' enables the host-browser bridge (prints the "
                        "setup command if needed). 'stop' reverts to the "
                        "container browser. 'status' reports current mode."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser import event_bus, host_bridge
        from lazyclaw.browser.browser_settings import (
            get_browser_settings, update_browser_settings,
        )

        if not self._config:
            return "Error: configuration unavailable."

        action = (params or {}).get("action", "start").lower()
        settings = await get_browser_settings(self._config, user_id)
        port = getattr(self._config, "cdp_port", 9222)

        # ─── status ─────────────────────────────────────────────────────
        if action == "status":
            mode = settings.get("use_host_browser", "off")
            last_source = settings.get("last_host_cdp_source")
            ws = await host_bridge.probe_host_cdp(port)
            reachable = "yes" if ws else "no"
            runtime = "docker" if host_bridge.is_docker_runtime() else "native"
            return (
                f"Host browser bridge: mode={mode}, runtime={runtime}, "
                f"reachable_now={reachable}, last_source={last_source or 'never'}.\n"
                + ("Use 'use my browser' to enable." if mode == "off"
                   else "Say 'stop host browser' to revert to the container Brave.")
            )

        # ─── stop ───────────────────────────────────────────────────────
        if action == "stop":
            await update_browser_settings(
                self._config, user_id, {"use_host_browser": "off"},
            )
            event_bus.publish(event_bus.BrowserEvent(
                user_id=user_id,
                kind="host_cdp",
                detail="Host browser bridge stopped — next action uses the container browser",
                extra={"source": "local"},
            ))
            return (
                "Host browser bridge stopped. Your next browser action will "
                "use the containerised Brave again. Your host Brave stays open — "
                "we never launched it, we only connected to it."
            )

        # ─── start ──────────────────────────────────────────────────────
        # Generate a token if we don't already have one — it's scoped per user.
        token = settings.get("host_cdp_token") or host_bridge.generate_host_token()
        if token != settings.get("host_cdp_token"):
            await update_browser_settings(
                self._config, user_id, {"host_cdp_token": token},
            )

        # Flip the preference on. Probe happens below — if host isn't reachable
        # we still persist the intent so the next connect will retry.
        await update_browser_settings(
            self._config, user_id, {"use_host_browser": "auto"},
        )

        ws_url = await host_bridge.probe_host_cdp(port)
        if ws_url:
            event_bus.publish(event_bus.BrowserEvent(
                user_id=user_id,
                kind="host_cdp",
                detail="Using your real Brave on the host",
                extra={"source": "host"},
            ))
            return (
                "Host browser bridge is ON. "
                "The agent now drives your real Brave with all your cookies. "
                "Say 'stop host browser' when you want to switch back."
            )

        # Not reachable yet — show the setup one-liner.
        command = host_bridge.build_launch_command(token)
        warning = host_bridge.security_warning()
        return (
            "Host browser bridge is armed but your host Brave isn't reachable "
            f"on port {port} yet. To connect:\n\n"
            f"```\n{command}\n```\n\n"
            f"{warning}\n\n"
            "Once Brave is running with the command above, say 'use my browser' "
            "again (or any browser action) and I'll latch onto it."
        )
