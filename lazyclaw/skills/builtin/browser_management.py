"""Browser persistence skill — set browser mode: off / auto / on."""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class BrowserSetPersistentSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def name(self) -> str:
        return "browser_set_persistent"

    @property
    def description(self) -> str:
        return (
            "Set browser persistence mode. "
            "'on' keeps browser always running in background. "
            "'auto' keeps browser alive after use, closes after 10 min idle. "
            "'off' launches browser on-demand only. "
            "Use when user says 'keep browser open', 'browser always on', "
            "'auto browser', 'close browser', or 'stop persistent browser'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["off", "auto", "on"],
                    "description": (
                        "'on' = always running, "
                        "'auto' = stays alive after use then closes when idle, "
                        "'off' = on-demand only"
                    ),
                },
            },
            "required": ["mode"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.browser.browser_settings import update_browser_settings

            mode = params["mode"]
            await update_browser_settings(
                self._config, user_id, {"persistent": mode},
            )

            if mode == "on":
                await self._ensure_browser_running(user_id)
                return (
                    "Browser mode: ON. Brave is running in the background — "
                    "browser is instant, cron jobs can use the browser "
                    "without startup delay."
                )
            elif mode == "auto":
                return (
                    "Browser mode: AUTO. Browser will stay alive after use "
                    "and close automatically after 10 minutes idle. "
                    "Best balance of speed and resources."
                )
            else:
                await self._stop_browser()
                return (
                    "Browser mode: OFF. Browser will shut down now "
                    "and only launch on-demand when needed."
                )
        except Exception as exc:
            return f"Error: {exc}"

    async def _ensure_browser_running(self, user_id: str) -> None:
        """Launch headless browser with CDP if not already running."""
        from lazyclaw.browser.cdp import find_chrome_cdp
        from lazyclaw.browser.cdp_backend import CDPBackend

        port = getattr(self._config, "cdp_port", 9222)
        ws_url = await find_chrome_cdp(port)
        if ws_url:
            return  # Already running

        profile_dir = str(
            self._config.database_dir / "browser_profiles" / user_id
        )
        backend = CDPBackend(port=port, profile_dir=profile_dir)
        await backend._auto_launch_chrome()

    async def _stop_browser(self) -> None:
        """Kill the persistent headless browser."""
        import asyncio

        port = getattr(self._config, "cdp_port", 9222)
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", f"--remote-debugging-port={port}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as exc:
            logger.debug("pkill browser process failed (may already be gone): %s", exc)


class BrowserApproveConnectSkill(BaseSkill):
    """Approve browser restart with CDP for tab access."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "approve_browser_connect"

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def description(self) -> str:
        return (
            "Approve restarting Brave with debugging so the agent can read "
            "browser tabs. Use when user says 'yes connect browser', "
            "'approve browser', 'allow browser access', or 'yes' after "
            "being asked about browser restart."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"

        from lazyclaw.browser.browser_settings import update_browser_settings
        from lazyclaw.browser.cdp_backend import restart_browser_with_cdp

        # Remember approval
        await update_browser_settings(
            self._config, user_id, {"cdp_approved": True},
        )

        # Restart Brave with CDP
        port = getattr(self._config, "cdp_port", 9222)
        profile_dir = str(
            self._config.database_dir / "browser_profiles" / user_id
        )
        ws_url = await restart_browser_with_cdp(port=port, profile_dir=profile_dir)

        if ws_url:
            return (
                "Browser restarted with debugging enabled. I can now read "
                "any open tab. This permission is "
                "saved — I'll auto-connect next time."
            )
        return "Failed to restart browser. Check if Brave is installed."
