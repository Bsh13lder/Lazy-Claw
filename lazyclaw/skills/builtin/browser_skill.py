"""Unified browser skill — single CDP-based tool for all browser interactions.

Actions: read, open, click, type, screenshot, tabs, scroll.
Controls user's real Brave browser via Chrome DevTools Protocol.
"""

from __future__ import annotations

import asyncio
import logging

from lazyclaw.browser.browser_settings import touch_browser_activity
from lazyclaw.browser.page_reader import run_extractor, _detect_page_type
from lazyclaw.runtime.tool_result import Attachment, ToolResult
from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Shared CDP backend instance (lazy-initialized, on-demand)
_cdp_backend = None

# ── Shortcut mapping ────────────────────────────────────────────────────

_SHORTCUTS = {
    "whatsapp": "https://web.whatsapp.com",
    "wa": "https://web.whatsapp.com",
    "gmail": "https://mail.google.com",
    "mail": "https://mail.google.com",
    "email": "https://mail.google.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
}


def _query_to_url(query: str) -> str:
    """Convert a target like 'whatsapp' to a URL."""
    q = query.lower().strip()
    if q in _SHORTCUTS:
        return _SHORTCUTS[q]
    if q.startswith("http"):
        return q
    if "." in q:
        return f"https://{q}"
    return ""


# ── CDP backend helpers ─────────────────────────────────────────────────

async def _get_cdp_backend(user_id: str = "default"):
    """Get or create the CDP backend for a user.

    Lazy singleton — recreates if user_id profile changed.
    """
    global _cdp_backend
    from lazyclaw.browser.cdp_backend import CDPBackend
    from lazyclaw.config import load_config

    config = load_config()
    port = getattr(config, "cdp_port", 9222)
    profile_dir = str(config.database_dir / "browser_profiles" / user_id)

    if _cdp_backend is None or _cdp_backend._profile_dir != profile_dir:
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
    return _cdp_backend


async def _get_visible_cdp_backend(user_id: str = "default"):
    """Ensure visible Brave is running with CDP and return backend.

    - If CDP already available on port → reuse (don't kill!)
    - If nothing running → launch visible Brave with CDP profile
    """
    from lazyclaw.browser.cdp import find_chrome_cdp
    from lazyclaw.config import load_config

    config = load_config()
    port = getattr(config, "cdp_port", 9222)
    profile_dir = str(config.database_dir / "browser_profiles" / user_id)
    global _cdp_backend

    # Check if Brave is already running with CDP
    ws_url = await find_chrome_cdp(port)
    if ws_url:
        # Already running — reuse it (preserves WhatsApp login, cookies, etc.)
        logger.info("Brave already on CDP port %d, reusing", port)
        from lazyclaw.browser.cdp_backend import CDPBackend
        if _cdp_backend is None or _cdp_backend._profile_dir != profile_dir:
            _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
        return _cdp_backend

    # Nothing running — launch VISIBLE Brave with CDP profile
    chrome_bin = config.browser_executable or "google-chrome"
    import os
    os.makedirs(profile_dir, exist_ok=True)

    await asyncio.create_subprocess_exec(
        chrome_bin,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--disable-blink-features=AutomationControlled",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    logger.info("Launched VISIBLE Brave (port=%d, profile=%s)", port, profile_dir)

    # Wait for CDP to respond (up to 10s)
    for _ in range(20):
        await asyncio.sleep(0.5)
        if await find_chrome_cdp(port):
            break

    from lazyclaw.browser.cdp_backend import CDPBackend
    _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
    return _cdp_backend


# ── Unified BrowserSkill ────────────────────────────────────────────────

class BrowserSkill(BaseSkill):
    """Single CDP-based tool for all browser interactions.

    Pure action tool — buy tickets, check in, pay bills, order from Amazon,
    read WhatsApp/Gmail, navigate. Controls user's real visible Brave.
    """

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "browser"

    @property
    def display_name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Control the user's REAL Brave browser. This is the user's visible browser "
            "on their screen. Use for reading pages, navigating, clicking, typing, "
            "taking screenshots, listing tabs, scrolling. Supports shortcuts: "
            "'whatsapp', 'gmail', 'instagram', 'twitter', 'facebook', 'linkedin'. "
            "For WhatsApp, Gmail, or any logged-in site — ALWAYS use this tool."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "open", "click", "type", "screenshot", "tabs", "scroll", "close"],
                    "description": (
                        "read: silently read page content (invisible, no browser window shown). "
                        "open: OPEN visible Brave on screen. Use when user says 'open', 'show me', 'make visible', 'launch browser'. "
                        "click: click CSS selector. "
                        "type: type text into CSS selector. "
                        "screenshot: capture screenshot (ONLY when user asks). "
                        "tabs: list all open tabs. "
                        "scroll: scroll up or down. "
                        "close: close/hide the browser. Use when user says 'close browser', 'hide browser', 'background', 'minimize'."
                    ),
                },
                "target": {
                    "type": "string",
                    "description": (
                        "For read/open: URL, shortcut (whatsapp, gmail, etc), or tab query. "
                        "For click/type: CSS selector. "
                        "Leave empty for current tab."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (for 'type' action only).",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction (default: down).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, user_id: str, params: dict) -> str | ToolResult:
        action = params.get("action", "")
        touch_browser_activity()

        try:
            if action == "read":
                return await self._action_read(user_id, params)
            elif action == "open":
                return await self._action_open(user_id, params)
            elif action == "click":
                return await self._action_click(user_id, params)
            elif action == "type":
                return await self._action_type(user_id, params)
            elif action == "screenshot":
                return await self._action_screenshot(user_id, params)
            elif action == "tabs":
                return await self._action_tabs(user_id, params)
            elif action == "scroll":
                return await self._action_scroll(user_id, params)
            elif action == "close":
                return await self._action_close(user_id, params)
            else:
                return f"Unknown action: {action}. Use: read, open, click, type, screenshot, tabs, scroll, close"
        except ConnectionError:
            logger.info("browser: CDP unavailable, attempting auto-connect")
            return await self._auto_connect_and_retry(user_id, params)
        except Exception as e:
            logger.error("browser %s failed: %s", action, e, exc_info=True)
            return f"Error: {e}"

    # ── Action handlers ─────────────────────────────────────────────────

    async def _action_read(self, user_id: str, params: dict) -> str:
        """Read content from current tab or navigate+read a target.

        Connects to existing Brave via CDP. If no browser is running,
        asks user to launch with action='open' first — never auto-launches
        headless (headless can't maintain WhatsApp/Gmail sessions).
        """
        from lazyclaw.browser.cdp import find_chrome_cdp
        from lazyclaw.config import load_config

        config = load_config()
        port = getattr(config, "cdp_port", 9222)

        # Check if any browser is running on CDP port
        ws_url = await find_chrome_cdp(port)
        if not ws_url:
            return (
                "No browser running. Use browser(action='open') first to "
                "launch Brave on screen, then I can read pages from it."
            )

        backend = await _get_cdp_backend(user_id)
        target = params.get("target", "").strip()

        if target:
            # Try finding an open tab matching the target
            tab_list = await backend.tabs()
            match = next(
                (t for t in tab_list
                 if target.lower() in t.title.lower()
                 or target.lower() in t.url.lower()),
                None,
            )
            if match:
                await backend.switch_tab(match.id)
            else:
                # Auto-navigate to the target
                nav_url = _query_to_url(target)
                if nav_url:
                    logger.info("No tab '%s', navigating to %s", target, nav_url)
                    await backend.goto(nav_url)
                    await asyncio.sleep(3)
                else:
                    return f"No tab found matching '{target}' and couldn't resolve to a URL."

        # Use the JS extractor system for structured content
        result = await run_extractor(backend)
        title = result.get("title", "")
        url = result.get("url", "")
        text = result.get("text", "")
        page_type = result.get("type", "generic")

        summary = f"Tab: {title}\nURL: {url}"
        if page_type == "whatsapp" and result.get("unread_count"):
            summary += f"\nUnread: {result['unread_count']}"
        summary += f"\n\n{text}"
        return summary

    async def _action_open(self, user_id: str, params: dict) -> str:
        """Open visible Brave and navigate to target."""
        target = params.get("target", "").strip()
        if not target:
            # Just open Brave, no navigation
            await _get_visible_cdp_backend(user_id)
            return "Done — Brave is open on your screen."

        nav_url = _query_to_url(target)
        if not nav_url:
            return f"Couldn't resolve '{target}' to a URL."

        backend = await _get_visible_cdp_backend(user_id)

        # Check if target is already open in an existing tab
        try:
            tab_list = await backend.tabs()
            match = next(
                (t for t in tab_list
                 if target.lower() in t.title.lower()
                 or target.lower() in t.url.lower()
                 or nav_url.split("//")[-1].split("/")[0] in t.url),
                None,
            )
            if match:
                await backend.switch_tab(match.id)
                return (
                    f"Done — {match.title} is now on the user's screen in Brave. "
                    "No screenshot needed — they can see it."
                )
        except Exception:
            pass

        # Not open — navigate to it (with extra wait after fresh launch)
        await asyncio.sleep(2)
        try:
            await backend.goto(nav_url)
        except TimeoutError:
            # Brave might still be loading — retry once
            await asyncio.sleep(3)
            await backend.goto(nav_url)

        title = await backend.title()
        return (
            f"Done — {title} is now open on the user's screen in Brave. "
            "No screenshot needed — they can see it."
        )

    async def _action_click(self, user_id: str, params: dict) -> str:
        """Click an element by CSS selector."""
        selector = params.get("target", "").strip()
        if not selector:
            return "CSS selector required for click action (pass as 'target')."

        backend = await _get_cdp_backend(user_id)
        await backend.click(selector)
        return f"Clicked: {selector}"

    async def _action_type(self, user_id: str, params: dict) -> str:
        """Type text into an element by CSS selector."""
        selector = params.get("target", "").strip()
        text = params.get("text", "")
        if not selector or not text:
            return "Both target (CSS selector) and text required for type action."

        backend = await _get_cdp_backend(user_id)
        await backend.type_text(selector, text)
        return f"Typed '{text[:30]}...' into {selector}"

    async def _action_screenshot(self, user_id: str, params: dict) -> ToolResult:
        """Take a screenshot of the current tab."""
        backend = await _get_cdp_backend(user_id)
        url = await backend.current_url()
        title = await backend.title()
        ss_bytes = await backend.screenshot()
        return ToolResult(
            text=(
                f"Screenshot of: {title}\nURL: {url}\n"
                f"[{len(ss_bytes)} bytes, {len(ss_bytes) // 1024}KB PNG]"
            ),
            attachments=(
                Attachment(
                    data=ss_bytes,
                    media_type="image/png",
                    filename="screenshot.png",
                ),
            ),
        )

    async def _action_tabs(self, user_id: str, params: dict) -> str:
        """List all open tabs."""
        backend = await _get_cdp_backend(user_id)
        tab_list = await backend.tabs()
        if not tab_list:
            return "No tabs found. Is Brave/Chrome running?"

        lines = [f"Open tabs ({len(tab_list)}):"]
        for i, tab in enumerate(tab_list, 1):
            active = " (active)" if tab.active else ""
            lines.append(f"  {i}. {tab.title}{active}")
            lines.append(f"     {tab.url}")
        return "\n".join(lines)

    async def _action_scroll(self, user_id: str, params: dict) -> str:
        """Scroll the page up or down."""
        direction = params.get("direction", "down")
        backend = await _get_cdp_backend(user_id)
        await backend.scroll(direction)
        return f"Scrolled {direction}"

    async def _action_close(self, user_id: str, params: dict) -> str:
        """Close/hide the browser."""
        from lazyclaw.browser.cdp import find_chrome_cdp
        from lazyclaw.config import load_config

        config = load_config()
        port = getattr(config, "cdp_port", 9222)
        global _cdp_backend

        if not await find_chrome_cdp(port):
            return "Browser is not running."

        try:
            proc = await asyncio.create_subprocess_shell(
                f"ps aux | grep 'remote-debugging-port={port}' | grep -v grep | awk '{{print $2}}'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            for pid in stdout.decode().strip().split("\n"):
                pid = pid.strip()
                if pid and pid.isdigit():
                    await asyncio.create_subprocess_exec(
                        "kill", pid,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
            _cdp_backend = None
            return "Browser closed. Cookies saved — next open will restore your sessions."
        except Exception as e:
            return f"Error closing browser: {e}"

    # ── Auto-connect logic ──────────────────────────────────────────────

    async def _auto_connect_and_retry(self, user_id: str, params: dict) -> str:
        """Auto-restart Brave with CDP if approved, then retry."""
        from lazyclaw.browser.browser_settings import get_browser_settings
        from lazyclaw.config import load_config

        config = load_config()
        settings = await get_browser_settings(config, user_id)

        if not settings.get("cdp_approved"):
            return (
                "I need to restart Brave with debugging enabled so I can "
                "read your browser tabs (WhatsApp, Gmail, etc). All your "
                "tabs and logins will be preserved — just a 2-3 second "
                "restart. Say 'yes, connect browser' to allow. I'll "
                "remember your choice for next time."
            )

        from lazyclaw.browser.cdp_backend import restart_browser_with_cdp

        port = getattr(config, "cdp_port", 9222)
        profile_dir = str(config.database_dir / "browser_profiles" / user_id)
        ws_url = await restart_browser_with_cdp(port=port, profile_dir=profile_dir)

        if not ws_url:
            return "Failed to restart browser with debugging. Check if Brave is installed."

        global _cdp_backend
        from lazyclaw.browser.cdp_backend import CDPBackend
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)

        try:
            return await self.execute(user_id, params)
        except Exception as e:
            return f"Browser restarted but action failed: {e}"
