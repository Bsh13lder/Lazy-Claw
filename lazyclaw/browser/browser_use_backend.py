"""Browser-Use backend — optional alternative to raw CDPBackend.

Uses the browser-use library (pip install browser-use) which wraps Playwright
with optimized DOM extraction and element interaction. Provides the same
interface as CDPBackend so it's a drop-in replacement.

Advantages over raw CDP:
- Battle-tested DOM extractor (86K+ GitHub stars)
- Built-in element highlighting and interaction
- Better handling of Shadow DOM, iframes, dynamic content
- 89% WebVoyager benchmark score

Trade-offs:
- Requires Playwright (~200MB) as dependency
- Slightly higher latency (Playwright relay layer)
- No raw CDP access (stealth injection via Playwright instead)

Enable via: browser settings → backend = "browser_use"
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Lazy import flag — set after first successful import
_BROWSER_USE_AVAILABLE: bool | None = None


def is_available() -> bool:
    """Check if browser-use is installed."""
    global _BROWSER_USE_AVAILABLE
    if _BROWSER_USE_AVAILABLE is None:
        try:
            import browser_use  # noqa: F401
            _BROWSER_USE_AVAILABLE = True
        except ImportError:
            _BROWSER_USE_AVAILABLE = False
    return _BROWSER_USE_AVAILABLE


@dataclass(frozen=True)
class TabInfo:
    """Immutable info about a browser tab (matches CDPBackend.TabInfo)."""
    id: str
    title: str
    url: str
    active: bool = False


class BrowserUseBackend:
    """Browser control via browser-use library (Playwright-based).

    Same interface as CDPBackend — goto(), click(), type_text(), etc.
    Lazy-initialized: imports browser-use only on first use.
    """

    def __init__(self, headless: bool = True, profile_dir: str | None = None) -> None:
        self._headless = headless
        self._profile_dir = profile_dir
        self._browser = None
        self._context = None
        self._connect_lock = asyncio.Lock()

    async def _ensure_connected(self):
        """Lazy-init browser-use Browser + BrowserContext."""
        if self._context is not None:
            return self._context

        async with self._connect_lock:
            if self._context is not None:
                return self._context

            from browser_use.browser.browser import Browser, BrowserConfig
            from browser_use.browser.context import BrowserContext, BrowserContextConfig

            browser_config = BrowserConfig(
                headless=self._headless,
                disable_security=False,
                extra_chromium_args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            ctx_config = BrowserContextConfig(
                browser_window_size={"width": 1280, "height": 720},
                highlight_elements=False,  # No visual overlays in production
                viewport_expansion=500,
            )

            self._browser = Browser(config=browser_config)
            self._context = BrowserContext(
                browser=self._browser,
                config=ctx_config,
            )

            # Initialize the context (opens browser)
            await self._context.__aenter__()

            logger.info(
                "BrowserUse backend connected (headless=%s, profile=%s)",
                self._headless, self._profile_dir,
            )
            return self._context

    async def _get_page(self):
        """Get the current Playwright page from browser-use context."""
        ctx = await self._ensure_connected()
        return await ctx.get_current_page()

    # ── Navigation ────────────────────────────────────────────────────

    async def goto(self, url: str) -> None:
        page = await self._get_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(0.8, 1.5))

    async def current_url(self) -> str:
        page = await self._get_page()
        return page.url

    async def title(self) -> str:
        page = await self._get_page()
        return await page.title()

    async def content(self) -> str:
        page = await self._get_page()
        return await page.content()

    async def evaluate(self, js: str) -> Any:
        page = await self._get_page()
        return await page.evaluate(js)

    # ── Capture ───────────────────────────────────────────────────────

    async def screenshot(self, full_page: bool = False) -> bytes:
        page = await self._get_page()
        return await page.screenshot(full_page=full_page, type="png")

    # ── Interaction ───────────────────────────────────────────────────

    async def click(self, selector: str) -> None:
        page = await self._get_page()
        await page.click(selector, timeout=10000)
        await asyncio.sleep(random.uniform(0.2, 0.5))

    async def type_text(self, selector: str, text: str) -> None:
        page = await self._get_page()
        await page.click(selector, timeout=10000)
        await asyncio.sleep(0.1)
        await page.keyboard.type(text, delay=random.uniform(30, 80))

    async def press_key(self, key: str) -> None:
        """Press a keyboard key."""
        key_map = {
            "enter": "Enter", "return": "Enter",
            "escape": "Escape", "esc": "Escape",
            "tab": "Tab", "backspace": "Backspace",
            "delete": "Delete", "space": " ",
            "arrowup": "ArrowUp", "arrowdown": "ArrowDown",
            "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
        }
        mapped = key_map.get(key.lower().replace(" ", ""), key)
        page = await self._get_page()
        await page.keyboard.press(mapped)

    async def scroll(self, direction: str = "down", amount: int = 300) -> None:
        page = await self._get_page()
        delta = amount if direction == "down" else -amount
        await page.mouse.wheel(0, delta)
        await asyncio.sleep(random.uniform(0.3, 0.6))

    async def hover(self, selector: str) -> None:
        page = await self._get_page()
        await page.hover(selector, timeout=10000)

    async def drag_and_drop(self, source_selector: str, target_selector: str) -> None:
        page = await self._get_page()
        await page.drag_and_drop(source_selector, target_selector)

    # ── Tab management ────────────────────────────────────────────────

    async def tabs(self) -> list[TabInfo]:
        ctx = await self._ensure_connected()
        page = await self._get_page()
        current_url = page.url

        # browser-use exposes tabs via get_state
        try:
            state = await ctx.get_state()
            result = []
            for i, tab in enumerate(state.tabs):
                result.append(TabInfo(
                    id=str(i),
                    title=getattr(tab, "title", ""),
                    url=getattr(tab, "url", ""),
                    active=(getattr(tab, "url", "") == current_url),
                ))
            return result
        except Exception as exc:
            logger.debug("browser-use tabs() failed, falling back: %s", exc)
            return [TabInfo(id="0", title=await self.title(), url=current_url, active=True)]

    async def switch_tab(self, tab_id: str) -> None:
        ctx = await self._ensure_connected()
        try:
            await ctx.switch_to_tab(int(tab_id))
        except Exception as exc:
            logger.warning("switch_tab(%s) failed: %s", tab_id, exc)

    # ── Accessibility ─────────────────────────────────────────────────

    async def get_state(self):
        """Get browser-use's optimized page state (DOM tree + screenshot).

        This is browser-use's unique value — its DOM extractor is more
        sophisticated than raw accessibility tree dumps.
        """
        ctx = await self._ensure_connected()
        return await ctx.get_state()

    async def find_element_by_role(self, description: str) -> dict | None:
        """Find element using Playwright's role-based locators."""
        page = await self._get_page()
        try:
            # Try Playwright's getByRole with name matching
            for role in ("button", "link", "textbox", "checkbox", "tab", "menuitem"):
                locator = page.get_by_role(role, name=description)
                count = await locator.count()
                if count > 0:
                    box = await locator.first.bounding_box()
                    if box:
                        return {
                            "x": box["x"] + box["width"] / 2,
                            "y": box["y"] + box["height"] / 2,
                            "role": role,
                            "name": description,
                        }
        except Exception as exc:
            logger.debug("find_element_by_role failed: %s", exc)
        return None

    async def click_by_role(self, description: str) -> dict | None:
        """Find and click element by role/label."""
        match = await self.find_element_by_role(description)
        if match:
            page = await self._get_page()
            await page.mouse.click(match["x"], match["y"])
            await asyncio.sleep(random.uniform(0.2, 0.5))
            return {"role": match["role"], "name": match["name"]}
        return None

    async def wait_for_selector(self, selector: str, timeout_ms: int = 5000) -> bool:
        page = await self._get_page()
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            return False

    # ── Console ───────────────────────────────────────────────────────

    async def inject_console_capture(self) -> None:
        page = await self._get_page()
        await page.evaluate("""(() => {
            if (window.__lazyclaw_console_hooked) return;
            window.__lazyclaw_console_logs = [];
            const orig = {log: console.log, warn: console.warn, error: console.error};
            for (const [level, fn] of Object.entries(orig)) {
                console[level] = function(...args) {
                    window.__lazyclaw_console_logs.push({
                        level, text: args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' '),
                        time: Date.now()
                    });
                    if (window.__lazyclaw_console_logs.length > 100)
                        window.__lazyclaw_console_logs.shift();
                    fn.apply(console, args);
                };
            }
            window.__lazyclaw_console_hooked = true;
        })()""")

    async def get_console_logs(self, clear: bool = True) -> list[dict]:
        page = await self._get_page()
        import json
        raw = await page.evaluate("JSON.stringify(window.__lazyclaw_console_logs || [])")
        try:
            logs = json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            logs = []
        if clear:
            await page.evaluate("window.__lazyclaw_console_logs = [];")
        return logs

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def is_connected(self) -> bool:
        return self._context is not None

    async def close(self) -> None:
        if self._context:
            try:
                await self._context.__aexit__(None, None, None)
            except Exception:
                logger.debug("browser-use context close failed", exc_info=True)
            self._context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("browser-use browser close failed", exc_info=True)
            self._browser = None
        logger.info("BrowserUse backend disconnected")

    @property
    def backend_type(self) -> str:
        return "browser_use"
