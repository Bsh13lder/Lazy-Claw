"""Real browser skills — control user's actual Chrome via CDP.

These skills connect on-demand to the user's running Chrome browser.
They coexist with Playwright-based browser skills (browse_web, read_page)
which are better for automated/background tasks.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from lazyclaw.runtime.tool_result import Attachment, ToolResult
from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Shared CDP backend instance (lazy-initialized, on-demand)
_cdp_backend = None


async def _get_cdp_backend():
    """Get or create the shared CDP backend instance.

    Uses the same profile directory as Playwright so cookies are shared.
    """
    global _cdp_backend
    if _cdp_backend is None:
        from lazyclaw.browser.cdp_backend import CDPBackend
        from lazyclaw.config import load_config

        config = load_config()
        port = getattr(config, "cdp_port", 9222)
        # Share profile with Playwright (default user)
        profile_dir = str(config.database_dir / "browser_profiles" / "default")
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
    return _cdp_backend


async def _get_visible_cdp_backend():
    """Launch visible Chrome (user wants to see the browser).

    Kills any existing headless Chrome first, then launches visible.
    Uses the same shared profile for cookie persistence.
    """
    import asyncio
    import os

    from lazyclaw.browser.cdp import find_chrome_cdp
    from lazyclaw.config import load_config

    config = load_config()
    port = getattr(config, "cdp_port", 9222)
    profile_dir = str(config.database_dir / "browser_profiles" / "default")

    # Kill existing headless Chrome on this port
    try:
        proc = await asyncio.create_subprocess_exec(
            "pkill", "-f", f"--headless.*--remote-debugging-port={port}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        await asyncio.sleep(1)
    except Exception:
        pass

    # Check if visible Chrome is already on the port
    ws_url = await find_chrome_cdp(port)
    if not ws_url:
        # Launch visible Chrome
        mac_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        chrome_bin = mac_chrome if os.path.exists(mac_chrome) else "google-chrome"

        await asyncio.create_subprocess_exec(
            chrome_bin,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("Launched VISIBLE Chrome (port=%d, profile=%s)", port, profile_dir)

        # Wait for it to start
        for _ in range(15):
            await asyncio.sleep(0.5)
            if await find_chrome_cdp(port):
                break

    # Reset the shared backend so it reconnects to the new Chrome
    global _cdp_backend
    from lazyclaw.browser.cdp_backend import CDPBackend
    _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)
    return _cdp_backend


class SeeBrowserSkill(BaseSkill):
    """Take a screenshot of the user's current browser tab + page summary."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "see_browser"

    @property
    def display_name(self) -> str:
        return "see_browser"

    @property
    def description(self) -> str:
        return (
            "Read page content from Chrome (auto-launches headless if not running). "
            "Set include_screenshot=true ONLY when user asks to see/send the page. "
            "NEVER use run_command to launch Chrome — this tool handles it automatically. "
            "For simple page reading, prefer browse_web (less RAM)."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "include_screenshot": {
                    "type": "boolean",
                    "description": "Include a screenshot. Only set true when user asks to SEE the page or you need to send an image. Default false.",
                    "default": True,
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str | ToolResult:
        backend = await _get_cdp_backend()
        try:
            url = await backend.current_url()
            title = await backend.title()

            # Get page text via JS (lightweight, no full HTML)
            text = await backend.evaluate("""
                (() => {
                    const sel = ['article', 'main', '[role="main"]', '.content', '#content', 'body'];
                    for (const s of sel) {
                        const el = document.querySelector(s);
                        if (el && el.innerText.trim().length > 50) {
                            return el.innerText.trim().substring(0, 3000);
                        }
                    }
                    return document.body?.innerText?.substring(0, 3000) || '';
                })()
            """)

            page_info = (
                f"Current browser tab:\n"
                f"Title: {title}\n"
                f"URL: {url}\n"
                f"\nPage content:\n{text}"
            )

            include_ss = params.get("include_screenshot", False)
            if include_ss:
                ss_bytes = await backend.screenshot()
                return ToolResult(
                    text=(
                        f"{page_info}\n\n"
                        f"[Screenshot captured: {len(ss_bytes)} bytes, "
                        f"{len(ss_bytes) // 1024}KB PNG]"
                    ),
                    attachments=(
                        Attachment(
                            data=ss_bytes,
                            media_type="image/png",
                            filename="screenshot.png",
                        ),
                    ),
                )

            return page_info
        except ConnectionError as e:
            return str(e)
        except Exception as e:
            logger.error("see_browser failed: %s", e, exc_info=True)
            return f"Error reading browser: {e}"


class ListTabsSkill(BaseSkill):
    """List all open tabs in the user's browser."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_tabs"

    @property
    def display_name(self) -> str:
        return "list_tabs"

    @property
    def description(self) -> str:
        return (
            "List all open tabs in the user's real browser. "
            "Shows title and URL for each tab. "
            "Use when user asks 'what tabs do I have open'."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        backend = await _get_cdp_backend()
        try:
            tab_list = await backend.tabs()
            if not tab_list:
                return "No tabs found. Is Chrome running with --remote-debugging-port?"

            lines = [f"Open tabs ({len(tab_list)}):"]
            for i, tab in enumerate(tab_list, 1):
                active = " (active)" if tab.active else ""
                lines.append(f"  {i}. {tab.title}{active}")
                lines.append(f"     {tab.url}")
            return "\n".join(lines)
        except ConnectionError as e:
            return str(e)
        except Exception as e:
            logger.error("list_tabs failed: %s", e, exc_info=True)
            return f"Error listing tabs: {e}"


class ReadTabSkill(BaseSkill):
    """Read content from a specific tab or the current one."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "read_tab"

    @property
    def display_name(self) -> str:
        return "read_tab"

    @property
    def description(self) -> str:
        return (
            "Read the text content of a browser tab. "
            "Can read the current tab or switch to one by title/URL match. "
            "Use when user asks to 'read this page' or 'what does this tab say'."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tab_query": {
                    "type": "string",
                    "description": (
                        "Optional: title or URL substring to find a specific tab. "
                        "Leave empty for current tab."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        backend = await _get_cdp_backend()
        try:
            query = params.get("tab_query", "").strip()
            if query:
                tab_list = await backend.tabs()
                match = next(
                    (t for t in tab_list
                     if query.lower() in t.title.lower()
                     or query.lower() in t.url.lower()),
                    None,
                )
                if not match:
                    return f"No tab found matching '{query}'."
                await backend.switch_tab(match.id)

            url = await backend.current_url()
            title = await backend.title()
            text = await backend.evaluate("""
                (() => {
                    const sel = ['article', 'main', '[role="main"]', '.content', '#content', 'body'];
                    for (const s of sel) {
                        const el = document.querySelector(s);
                        if (el && el.innerText.trim().length > 50) {
                            return el.innerText.trim().substring(0, 5000);
                        }
                    }
                    return document.body?.innerText?.substring(0, 5000) || '';
                })()
            """)
            return f"Tab: {title}\nURL: {url}\n\n{text}"
        except ConnectionError as e:
            return str(e)
        except Exception as e:
            logger.error("read_tab failed: %s", e, exc_info=True)
            return f"Error reading tab: {e}"


class SwitchTabSkill(BaseSkill):
    """Switch to a different browser tab."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "switch_tab"

    @property
    def display_name(self) -> str:
        return "switch_tab"

    @property
    def description(self) -> str:
        return (
            "Switch to a different tab in the user's browser by title or URL match."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Title or URL substring to find the tab",
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        backend = await _get_cdp_backend()
        try:
            query = params.get("query", "").strip()
            if not query:
                return "Please specify a tab to switch to (title or URL fragment)."

            tab_list = await backend.tabs()
            match = next(
                (t for t in tab_list
                 if query.lower() in t.title.lower()
                 or query.lower() in t.url.lower()),
                None,
            )
            if not match:
                available = ", ".join(t.title[:30] for t in tab_list[:5])
                return f"No tab matching '{query}'. Open tabs: {available}"

            await backend.switch_tab(match.id)
            return f"Switched to: {match.title} ({match.url})"
        except ConnectionError as e:
            return str(e)
        except Exception as e:
            logger.error("switch_tab failed: %s", e, exc_info=True)
            return f"Error switching tab: {e}"


class BrowserActionSkill(BaseSkill):
    """Perform actions in the user's real browser (click, type, scroll, navigate)."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "browser_action"

    @property
    def display_name(self) -> str:
        return "browser_action"

    @property
    def description(self) -> str:
        return (
            "Low-level Chrome action by CSS selector: click, type, scroll, navigate. "
            "ONLY use when user asks to SEE the browser on screen (visible=true) or "
            "you need a simple one-off click with a known CSS selector. "
            "For ALL interactive tasks (WhatsApp, Instagram, forms, logins), "
            "use browse_web instead — it finds elements intelligently."
        )

    @property
    def category(self) -> str:
        return "browser"

    @property
    def permission_hint(self) -> str:
        return "ask"  # Always ask before acting in real browser

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["click", "type", "scroll", "goto"],
                    "description": "The action to perform",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for click/type targets",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (for 'type' action) or URL (for 'goto')",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction (default: down)",
                },
                "visible": {
                    "type": "boolean",
                    "description": "Set true ONLY when user asks to SEE the browser on their screen (e.g. 'show me', 'open on my screen', 'I want to scan QR'). Default false (headless).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        visible = params.get("visible", False)
        if visible:
            backend = await _get_visible_cdp_backend()
        else:
            backend = await _get_cdp_backend()
        action = params.get("action", "")

        try:
            if action == "goto":
                url = params.get("text", "")
                if not url:
                    return "URL required for goto action."
                await backend.goto(url)
                title = await backend.title()
                return f"Navigated to: {title} ({url})"

            elif action == "click":
                selector = params.get("selector", "")
                if not selector:
                    return "CSS selector required for click action."
                await backend.click(selector)
                return f"Clicked: {selector}"

            elif action == "type":
                selector = params.get("selector", "")
                text = params.get("text", "")
                if not selector or not text:
                    return "Both selector and text required for type action."
                await backend.type_text(selector, text)
                return f"Typed '{text[:30]}...' into {selector}"

            elif action == "scroll":
                direction = params.get("direction", "down")
                await backend.scroll(direction)
                return f"Scrolled {direction}"

            else:
                return f"Unknown action: {action}. Use: click, type, scroll, goto"
        except ConnectionError as e:
            return str(e)
        except Exception as e:
            logger.error("browser_action %s failed: %s", action, e, exc_info=True)
            return f"Error: {e}"
