"""CDP-based BrowserBackend — controls user's real Chrome browser.

Implements BrowserBackend ABC using Chrome DevTools Protocol.
Connects to Chrome via ws://localhost:9222 (configurable port).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

from lazyclaw.browser.backend import BrowserBackend, TabInfo
from lazyclaw.browser.cdp import (
    CDPConnection,
    CDPTab,
    find_chrome_cdp,
    list_chrome_tabs,
)

logger = logging.getLogger(__name__)

# Idle timeout for CDP connections (5 minutes)
CDP_IDLE_TIMEOUT = 300


class CDPBackend(BrowserBackend):
    """Real Chrome browser control via Chrome DevTools Protocol.

    On-demand: connects lazily when first used, auto-disconnects on idle.
    Does NOT close the user's browser — only disconnects the WebSocket.
    """

    def __init__(self, port: int = 9222) -> None:
        self._port = port
        self._conn: CDPConnection | None = None
        self._current_tab: CDPTab | None = None
        self._last_activity: float = 0.0

    async def _ensure_connected(self) -> CDPConnection:
        """Lazy connect: discover Chrome and connect to active tab."""
        if self._conn and self._conn.is_connected:
            self._last_activity = time.monotonic()
            return self._conn

        ws_url = await find_chrome_cdp(self._port)
        if not ws_url:
            raise ConnectionError(
                f"Chrome not running with debugging port. Launch with:\n"
                f"  open -a 'Google Chrome' --args --remote-debugging-port={self._port}"
            )

        # Connect to the first (active) page tab
        tabs = await list_chrome_tabs(self._port)
        page_tabs = [t for t in tabs if t.tab_type == "page"]
        if not page_tabs:
            raise ConnectionError("Chrome has no open tabs.")

        self._current_tab = page_tabs[0]
        self._conn = CDPConnection()
        await self._conn.connect(self._current_tab.ws_url)

        # Enable required CDP domains
        await self._conn.send("Page.enable")
        await self._conn.send("Runtime.enable")

        self._last_activity = time.monotonic()
        logger.info(
            "CDP connected to tab: %s (%s)",
            self._current_tab.title, self._current_tab.url,
        )
        return self._conn

    async def goto(self, url: str) -> None:
        conn = await self._ensure_connected()
        await conn.send("Page.navigate", {"url": url})
        # Wait for page to load
        await asyncio.sleep(1)
        try:
            await conn.send(
                "Page.waitForNavigation",
                {"timeout": 10000},
            )
        except Exception:
            pass  # Some pages don't trigger navigation events

    async def current_url(self) -> str:
        conn = await self._ensure_connected()
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": "window.location.href", "returnByValue": True},
        )
        return result.get("result", {}).get("value", "")

    async def title(self) -> str:
        conn = await self._ensure_connected()
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": "document.title", "returnByValue": True},
        )
        return result.get("result", {}).get("value", "")

    async def content(self) -> str:
        conn = await self._ensure_connected()
        result = await conn.send(
            "Runtime.evaluate",
            {
                "expression": "document.documentElement.outerHTML",
                "returnByValue": True,
            },
        )
        return result.get("result", {}).get("value", "")

    async def evaluate(self, js: str) -> Any:
        conn = await self._ensure_connected()
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True, "awaitPromise": True},
        )
        inner = result.get("result", {})
        if inner.get("type") == "undefined":
            return None
        return inner.get("value", inner.get("description", ""))

    async def screenshot(self, full_page: bool = False) -> bytes:
        conn = await self._ensure_connected()
        params: dict = {"format": "png", "quality": 80}
        if full_page:
            # Get full page dimensions
            metrics = await conn.send("Page.getLayoutMetrics")
            content_size = metrics.get("contentSize", {})
            if content_size:
                params["clip"] = {
                    "x": 0, "y": 0,
                    "width": content_size.get("width", 1280),
                    "height": content_size.get("height", 720),
                    "scale": 1,
                }
        result = await conn.send("Page.captureScreenshot", params)
        b64_data = result.get("data", "")
        return base64.b64decode(b64_data)

    async def click(self, selector: str) -> None:
        conn = await self._ensure_connected()
        # Find element center coordinates via JS
        js = f"""
        (() => {{
            const el = document.querySelector({_js_str(selector)});
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{
                x: rect.x + rect.width / 2,
                y: rect.y + rect.height / 2
            }};
        }})()
        """
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )
        coords = result.get("result", {}).get("value")
        if not coords:
            raise ValueError(f"Element not found: {selector}")

        x, y = coords["x"], coords["y"]
        # Dispatch mouse events
        for event_type in ("mousePressed", "mouseReleased"):
            await conn.send("Input.dispatchMouseEvent", {
                "type": event_type,
                "x": x, "y": y,
                "button": "left",
                "clickCount": 1,
            })

    async def type_text(self, selector: str, text: str) -> None:
        conn = await self._ensure_connected()
        # Focus the element first
        await conn.send(
            "Runtime.evaluate",
            {
                "expression": (
                    f"document.querySelector({_js_str(selector)})?.focus()"
                ),
            },
        )
        await asyncio.sleep(0.1)
        # Type each character
        for char in text:
            await conn.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "text": char,
                "key": char,
            })
            await conn.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": char,
            })
            await asyncio.sleep(0.02)  # Human-like delay

    async def scroll(self, direction: str = "down", amount: int = 300) -> None:
        conn = await self._ensure_connected()
        delta_y = amount if direction == "down" else -amount
        await conn.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": 400, "y": 300,
            "deltaX": 0, "deltaY": delta_y,
        })

    async def wait_for_selector(
        self, selector: str, timeout_ms: int = 5000,
    ) -> bool:
        conn = await self._ensure_connected()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            result = await conn.send(
                "Runtime.evaluate",
                {
                    "expression": (
                        f"!!document.querySelector({_js_str(selector)})"
                    ),
                    "returnByValue": True,
                },
            )
            if result.get("result", {}).get("value"):
                return True
            await asyncio.sleep(0.2)
        return False

    async def tabs(self) -> list[TabInfo]:
        chrome_tabs = await list_chrome_tabs(self._port)
        current_id = self._current_tab.id if self._current_tab else ""
        return [
            TabInfo(
                id=t.id,
                title=t.title,
                url=t.url,
                active=(t.id == current_id),
            )
            for t in chrome_tabs
        ]

    async def switch_tab(self, tab_id: str) -> None:
        chrome_tabs = await list_chrome_tabs(self._port)
        target = next((t for t in chrome_tabs if t.id == tab_id), None)
        if not target:
            raise ValueError(f"Tab not found: {tab_id}")

        # Close old connection, connect to new tab
        if self._conn:
            await self._conn.close()

        self._current_tab = target
        self._conn = CDPConnection()
        await self._conn.connect(target.ws_url)
        await self._conn.send("Page.enable")
        await self._conn.send("Runtime.enable")

        # Bring tab to front
        await self._conn.send("Page.bringToFront")

        logger.info("Switched to tab: %s (%s)", target.title, target.url)

    async def is_connected(self) -> bool:
        if not self._conn:
            return False
        return self._conn.is_connected

    async def close(self) -> None:
        """Disconnect CDP — does NOT close the user's browser."""
        if self._conn:
            await self._conn.close()
            self._conn = None
        self._current_tab = None
        logger.info("CDP backend disconnected (browser still running)")

    @property
    def backend_type(self) -> str:
        return "cdp"


def _js_str(s: str) -> str:
    """Escape a Python string for safe use in JavaScript."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
