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
            "**Call this skill BEFORE `browser` whenever the user mentions "
            "MY/VISIBLE/REAL browser** — that's the signal they want their "
            "own session, not a fresh headless container.\n\n"
            "Trigger phrases (any of these should match — single word, "
            "typo, or other languages too):\n"
            "  - 'use my browser', 'use mybrowser', 'usemybrowser'\n"
            "  - 'use my brave', 'use brave', 'use mybrave', 'my brave'\n"
            "  - 'use my chrome', 'my chrome'\n"
            "  - 'work on my visible browser', 'my visible browser', "
            "'visible browser', 'in my browser', 'on my browser', "
            "'open in my browser', 'with my browser', 'work in my browser'\n"
            "  - 'login as me', 'connect to brave', 'use my cookies', "
            "'use my real browser', 'host browser', 'host brave', "
            "'real browser', 'logged-in browser'\n"
            "  - Spanish: 'usa mi navegador', 'mi navegador', 'usa mi brave', "
            "'en mi navegador'\n"
            "  - Georgian/Latin: 'ჩემი ბრაუზერი', 'chemi brauzeri'\n\n"
            "Keywords for search_tools: browser, brave, chrome, host, "
            "cookies, login, real, my, mine, mybrowser, mybrave, visible, "
            "logged-in.\n\n"
            "**Bridge vs container — when to use which:**\n"
            "  - 'my'/'visible'/'real' present → THIS skill (host bridge, "
            "real cookies, no Cloudflare challenges)\n"
            "  - 'show me' / 'i want to watch' WITHOUT 'my' → "
            "`browser(visible=true)` (container Brave + noVNC takeover, "
            "fresh profile, you'll see it but no cookies)\n\n"
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

        # Marker file written by scripts/install-host-brave-bridge.sh — when
        # present, the user has the launchd auto-start helper installed.
        # We branch our messaging on this so we stop dumping raw shell
        # commands to users who already set up the auto-bridge.
        marker_path = self._config.database_dir / ".host_bridge_installed"
        bridge_installed = marker_path.exists()

        # ─── status ─────────────────────────────────────────────────────
        if action == "status":
            mode = settings.get("use_host_browser", "off")
            last_source = settings.get("last_host_cdp_source")
            ws = await host_bridge.probe_host_cdp(port)
            reachable = "yes" if ws else "no"
            runtime = "docker" if host_bridge.is_docker_runtime() else "native"
            installer = "yes" if bridge_installed else "no"
            shared = "yes" if host_bridge.shared_host_token() else "no"
            return (
                f"Host browser bridge: mode={mode}, runtime={runtime}, "
                f"reachable_now={reachable}, launchd_helper_installed={installer}, "
                f"shared_token_in_env={shared}, last_source={last_source or 'never'}.\n"
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

        # First probe.
        ws_url = await host_bridge.probe_host_cdp(port)

        # Self-heal: if the bridge marker exists but the probe just failed,
        # leave a `bridge-repair-needed` flag in data/ so the host-side
        # watcher (or the user) can react. Then retry the probe a few times
        # with backoff — covers the "Brave is mid-launch" race when launchd
        # has just kicked Brave but the port isn't bound yet (~3-6s on cold
        # macOS starts).
        if not ws_url and bridge_installed:
            try:
                flag_path = self._config.database_dir / ".host_bridge_repair_needed"
                flag_path.write_text(
                    f"requested_at={int(__import__('time').time())}\n"
                    f"reason=cdp_unreachable_port_{port}\n",
                    encoding="utf-8",
                )
            except OSError:
                logger.debug("Could not write host-bridge repair flag", exc_info=True)

            event_bus.publish(event_bus.BrowserEvent(
                user_id=user_id,
                kind="host_cdp",
                detail="Host Brave unreachable — waiting for launchd to relaunch",
                extra={"source": "local"},
            ))

            import asyncio
            for delay in (2.0, 3.0, 4.0):
                await asyncio.sleep(delay)
                ws_url = await host_bridge.probe_host_cdp(port)
                if ws_url:
                    break

        if ws_url:
            # Clear repair flag if it was set during this call.
            try:
                flag_path = self._config.database_dir / ".host_bridge_repair_needed"
                if flag_path.exists():
                    flag_path.unlink()
            except OSError:
                pass

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

        # Still not reachable after retries. Two cases — the user has the
        # auto-start helper installed but Brave is genuinely down, or they
        # haven't set up the helper yet.
        if bridge_installed:
            return (
                "ACTION REQUIRED FROM USER (you cannot do this yourself — do NOT "
                "call `run_command`, `terminal`, or any shell tool; the container "
                f"cannot reach the user's host).\n\nHost bridge is armed but Brave "
                f"isn't responding on port {port} after a 9-second self-heal probe.\n\n"
                "**Reply to the user with this exact message and STOP — wait for them "
                "to confirm Brave is open before retrying anything:**\n\n"
                "> Your Brave isn't on debug port. Please open a Terminal on your "
                "Mac and run:\n"
                "> ```\n"
                "> bash scripts/repair-host-brave-bridge.sh\n"
                "> ```\n"
                "> Or just relaunch Brave yourself with --remote-debugging-port=9222. "
                "Tell me when it's done and I'll retry."
            )

        # No helper installed yet — recommend the one-time installer first,
        # then fall back to the manual command for users who don't want
        # auto-start.
        command = host_bridge.build_launch_command(token)
        warning = host_bridge.security_warning()
        return (
            "Host browser bridge is armed but your host Brave isn't reachable "
            f"on port {port} yet.\n\n"
            "**Recommended (one-time setup, never copy a command again):**\n"
            "```\n"
            "bash scripts/install-host-brave-bridge.sh\n"
            "docker compose restart lazyclaw\n"
            "```\n"
            "That installs a launchd agent so Brave auto-launches with the "
            "debug port on every login, and survives crashes. After it runs, "
            "say 'use my browser' again — connects first try.\n\n"
            "**Or — one-shot manual launch (have to repeat after every reboot):**\n"
            f"```\n{command}\n```\n\n"
            f"{warning}"
        )
