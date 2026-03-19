"""Real browser skills — control user's actual Chrome via CDP.

These skills connect on-demand to the user's running Chrome browser.
They coexist with Playwright-based browser skills (browse_web, read_page)
which are better for automated/background tasks.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from lazyclaw.browser.browser_settings import touch_browser_activity
from lazyclaw.browser.page_reader import JS_WHATSAPP, JS_EMAIL, _detect_page_type
from lazyclaw.runtime.tool_result import Attachment, ToolResult
from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Shared CDP backend instance (lazy-initialized, on-demand)
_cdp_backend = None


async def _get_cdp_backend(user_id: str = "default"):
    """Get or create the CDP backend for a user.

    Uses the same profile directory as Playwright so cookies are shared.
    Recreates if user_id changed (different user).
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
    profile_dir = str(config.database_dir / "browser_profiles" / user_id)

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
        # Launch visible browser (Brave > Chrome)
        chrome_bin = config.browser_executable or "google-chrome"

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
            "Take a screenshot or read the user's BRAVE browser. "
            "This controls the REAL visible Brave on the user's screen. "
            "Set include_screenshot=true when user asks to see/show the screen. "
            "When user says 'Brave', 'my browser', 'show me screen' — use THIS tool."
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
                    "description": "ONLY set true when user says 'send screenshot', 'take screenshot', or 'send me a picture'. The browser is visible on the user's screen — they can already see it. Default false.",
                    "default": False,
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str | ToolResult:
        touch_browser_activity()
        backend = await _get_cdp_backend(user_id)
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
        backend = await _get_cdp_backend(user_id)
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
            "Read content from the user's BRAVE browser. Instant (0.1s). "
            "ALWAYS use this FIRST for WhatsApp, Gmail, or any site. "
            "If the tab isn't open, it auto-navigates Brave there. "
            "This is the user's REAL browser — NOT a hidden Chrome. "
            "When user says 'Brave', 'check my WhatsApp', 'read my email' — use THIS."
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
        touch_browser_activity()
        backend = await _get_cdp_backend(user_id)
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
                    # Auto-navigate visible browser to the requested site
                    nav_url = self._query_to_url(query)
                    if nav_url:
                        import asyncio
                        logger.info("No tab '%s', navigating to %s", query, nav_url)
                        await backend.goto(nav_url)
                        await asyncio.sleep(3)
                        # WhatsApp needs extra sync time
                        if "whatsapp" in nav_url:
                            for _ in range(15):
                                count = await backend.evaluate(
                                    "(() => document.querySelectorAll("
                                    "'[data-testid=\"cell-frame-container\"]').length)()"
                                )
                                if count and count > 0:
                                    break
                                await asyncio.sleep(2)
                    else:
                        return f"No tab found matching '{query}'."
                else:
                    await backend.switch_tab(match.id)

            url = await backend.current_url()
            title = await backend.title()
            page_type = _detect_page_type(url)

            if page_type == "whatsapp":
                # Quick sync check — tab is usually already loaded
                for _ in range(5):
                    count = await backend.evaluate(
                        "(() => document.querySelectorAll("
                        "'[data-testid=\"cell-frame-container\"]').length)()"
                    )
                    if count and count > 0:
                        break
                    await asyncio.sleep(1)
                result = await backend.evaluate(f"({JS_WHATSAPP})()")
                if isinstance(result, dict):
                    summary = f"Tab: {result.get('title', title)}\nURL: {url}"
                    if result.get("unread_count"):
                        summary += f"\nUnread: {result['unread_count']}"
                    summary += f"\n\n{result.get('text', '')}"
                    return summary
                return f"Tab: {title}\nURL: {url}\n\n{result}"

            if page_type == "email":
                result = await backend.evaluate(f"({JS_EMAIL})()")
                text = result.get("text", "") if isinstance(result, dict) else str(result)
                return f"Tab: {title}\nURL: {url}\n\n{text}"

            # Generic extractor — all other sites
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
        except ConnectionError:
            # CDP not available — try auto-restart Brave with CDP
            logger.info("read_tab: CDP unavailable, attempting auto-connect")
            return await self._auto_connect_and_retry(user_id, params)
        except Exception as e:
            logger.error("read_tab failed: %s", e, exc_info=True)
            return f"Error reading tab: {e}"

    @staticmethod
    def _query_to_url(query: str) -> str:
        """Convert a tab query like 'whatsapp' to a URL."""
        q = query.lower().strip()
        shortcuts = {
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
        if q in shortcuts:
            return shortcuts[q]
        if q.startswith("http"):
            return q
        if "." in q:
            return f"https://{q}"
        return ""

    async def _auto_connect_and_retry(self, user_id: str, params: dict) -> str:
        """Auto-restart Brave with CDP if approved, then retry read_tab."""
        from lazyclaw.browser.browser_settings import (
            get_browser_settings,
            update_browser_settings,
        )
        from lazyclaw.config import load_config

        config = load_config()
        settings = await get_browser_settings(config, user_id)

        if not settings.get("cdp_approved"):
            # First time — ask for permission
            return (
                "I need to restart Brave with debugging enabled so I can "
                "read your browser tabs (WhatsApp, Gmail, etc). All your "
                "tabs and logins will be preserved — just a 2-3 second "
                "restart. Say 'yes, connect browser' to allow. I'll "
                "remember your choice for next time."
            )

        # Approved — restart Brave with CDP
        from lazyclaw.browser.cdp_backend import restart_browser_with_cdp

        port = getattr(config, "cdp_port", 9222)
        profile_dir = str(config.database_dir / "browser_profiles" / user_id)
        ws_url = await restart_browser_with_cdp(
            port=port, profile_dir=profile_dir,
        )

        if not ws_url:
            return "Failed to restart browser with debugging. Check if Brave is installed."

        # Reset CDP backend to use the new connection
        global _cdp_backend
        from lazyclaw.browser.cdp_backend import CDPBackend
        _cdp_backend = CDPBackend(port=port, profile_dir=profile_dir)

        # Retry the original read_tab
        try:
            return await self.execute(user_id, params)
        except Exception as e:
            return f"Browser restarted but read failed: {e}"


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
        touch_browser_activity()
        backend = await _get_cdp_backend(user_id)
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
            "Perform an action in the user's BRAVE browser: click, type, scroll, goto URL. "
            "This controls the REAL visible Brave on the user's screen. "
            "Use for clicking buttons, typing text, navigating to URLs in Brave. "
            "When user says 'click', 'go to', 'open in Brave' — use THIS tool."
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
        touch_browser_activity()
        backend = await _get_cdp_backend(user_id)
        action = params.get("action", "")

        try:
            if action == "goto":
                url = params.get("text", "")
                if not url:
                    return "URL required for goto action."
                await backend.goto(url)
                title = await backend.title()
                return f"Done — {title} is now open on the user's screen in Brave. No screenshot needed — they can see it."

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
