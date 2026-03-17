"""Real browser skills — control user's actual Chrome via CDP.

These skills connect on-demand to the user's running Chrome browser.
They coexist with Playwright-based browser skills (browse_web, read_page)
which are better for automated/background tasks.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Shared CDP backend instance (lazy-initialized, on-demand)
_cdp_backend = None


async def _get_cdp_backend():
    """Get or create the shared CDP backend instance."""
    global _cdp_backend
    if _cdp_backend is None:
        from lazyclaw.browser.cdp_backend import CDPBackend
        from lazyclaw.config import load_config

        config = load_config()
        port = getattr(config, "cdp_port", 9222)
        _cdp_backend = CDPBackend(port=port)
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
            "Take a screenshot of the user's real browser and read the page content. "
            "Use this when the user asks 'what am I looking at', 'what's on my screen', "
            "'read my browser', or similar. Requires Chrome with --remote-debugging-port."
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
                    "description": "Include a screenshot (default true)",
                    "default": True,
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
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

            include_ss = params.get("include_screenshot", True)
            screenshot_note = ""
            if include_ss:
                ss_bytes = await backend.screenshot()
                screenshot_note = (
                    f"\n\n[Screenshot captured: {len(ss_bytes)} bytes, "
                    f"{len(ss_bytes) // 1024}KB PNG]"
                )

            return (
                f"Current browser tab:\n"
                f"Title: {title}\n"
                f"URL: {url}\n"
                f"\nPage content:\n{text}"
                f"{screenshot_note}"
            )
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
            "Perform an action in the user's real browser: "
            "click an element, type text, scroll, or navigate to a URL. "
            "Use CSS selectors for click/type targets."
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
            },
            "required": ["action"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
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
