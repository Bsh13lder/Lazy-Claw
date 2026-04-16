"""Browser remote-takeover NL skill.

Lets the user say "show me browser", "let me drive", "give me VNC link",
"take control" from any channel (Telegram, web chat, CLI). Returns a noVNC
URL the user can open on phone/desktop to see and control the browser.
"""

from __future__ import annotations

import logging
import sys

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class ShareBrowserControlSkill(BaseSkill):
    """Start a remote VNC takeover session and return a shareable URL."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def name(self) -> str:
        return "share_browser_control"

    @property
    def description(self) -> str:
        return (
            "Open a remote-control link for the browser the agent is using. "
            "Returns a noVNC URL the user can tap on phone/desktop to "
            "see the live browser and take control. "
            "Use when the user says 'show me the browser', 'let me drive', "
            "'I'll do it', 'take control', 'give me VNC link', "
            "'show what you are doing in browser', or asks to see "
            "what the bot is doing live."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "status"],
                    "description": "'start' opens a fresh session, 'stop' ends it, 'status' just checks.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.browser import event_bus
        from lazyclaw.browser.remote_takeover import (
            get_active_session, is_remote_capable, is_server_mode,
            start_macos_remote_session, start_remote_session, stop_remote_session,
        )

        action = (params or {}).get("action", "start").lower()

        if action == "status":
            existing = get_active_session(user_id)
            if existing:
                return f"Browser remote session active: {existing.url}"
            if not is_remote_capable():
                return (
                    "Remote takeover not available on this host. "
                    "On macOS, enable Screen Sharing in System Settings → Sharing. "
                    "On Linux, install: xvfb, x11vnc, websockify, noVNC."
                )
            return "No remote session running. Say 'show me browser' to start one."

        if action == "stop":
            await stop_remote_session(user_id)
            event_bus.publish(event_bus.BrowserEvent(
                user_id=user_id,
                kind="takeover",
                detail="Remote takeover ended — agent will resume",
                extra={"url": None},
            ))
            return "Remote browser session stopped."

        # action == "start" (default)
        existing = get_active_session(user_id)
        if existing:
            event_bus.publish(event_bus.BrowserEvent(
                user_id=user_id,
                kind="takeover",
                detail="Remote takeover link",
                extra={"url": existing.url},
            ))
            return (
                f"Browser remote session is already running.\n"
                f"Open this link to see and control the browser:\n{existing.url}"
            )

        if not is_remote_capable():
            return (
                "Remote takeover not available on this host. "
                "On macOS, enable Screen Sharing in System Settings → Sharing. "
                "On Linux server, install: xvfb, x11vnc, websockify, noVNC."
            )

        if not self._config:
            return "Error: configuration unavailable."

        try:
            port = getattr(self._config, "cdp_port", 9222)
            profile_dir = str(self._config.database_dir / "browser_profiles" / user_id)
            if is_server_mode():
                session = await start_remote_session(
                    user_id=user_id,
                    cdp_port=port,
                    profile_dir=profile_dir,
                    browser_bin=self._config.browser_executable,
                )
            elif sys.platform == "darwin":
                session = await start_macos_remote_session(user_id)
            else:
                return (
                    "Remote takeover requires macOS Screen Sharing or Linux server mode."
                )
        except Exception as exc:
            logger.warning("share_browser_control failed: %s", exc)
            return f"Could not start remote session: {exc}"

        event_bus.publish(event_bus.BrowserEvent(
            user_id=user_id,
            kind="takeover",
            detail="Remote takeover started — open the link to drive the browser",
            extra={"url": session.url},
        ))
        return (
            "Remote browser session ready. "
            f"Tap to see and control:\n{session.url}\n\n"
            "Tip: send 'stop browser control' to end the session."
        )
