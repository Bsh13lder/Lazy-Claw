"""CDP browser backend — controls user's real Chrome/Brave browser.

Uses Chrome DevTools Protocol via ws://localhost:9222 (configurable port).
Standalone module — no ABC or Playwright dependency.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from lazyclaw.browser.cdp import (
    CDPConnection,
    CDPTab,
    find_chrome_cdp,
    list_chrome_tabs,
)

logger = logging.getLogger(__name__)

# Idle timeout for CDP connections (5 minutes)
CDP_IDLE_TIMEOUT = 300


@dataclass(frozen=True)
class TabInfo:
    """Immutable info about a browser tab."""

    id: str
    title: str
    url: str
    active: bool = False


class CDPBackend:
    """Real Chrome browser control via Chrome DevTools Protocol.

    On-demand: connects lazily when first used, auto-disconnects on idle.
    Does NOT close the user's browser — only disconnects the WebSocket.
    """

    def __init__(self, port: int = 9222, profile_dir: str | None = None) -> None:
        self._port = port
        self._profile_dir = profile_dir  # Shared with Playwright when set
        self._conn: CDPConnection | None = None
        self._current_tab: CDPTab | None = None
        self._last_activity: float = 0.0

    async def _ensure_connected(self) -> CDPConnection:
        """Lazy connect: discover Chrome and connect to active tab.

        If no Chrome is running, auto-launches headless Chrome.
        If Chrome has no tabs, creates a new blank tab automatically.
        """
        if self._conn and self._conn.is_connected:
            self._last_activity = time.monotonic()
            return self._conn

        ws_url = await find_chrome_cdp(self._port)
        if not ws_url:
            # Auto-launch headless Chrome instead of asking the user
            ws_url = await self._auto_launch_chrome()
            if not ws_url:
                raise ConnectionError(
                    f"Could not launch Chrome with debugging port {self._port}."
                )

        # Connect to the first (active) page tab
        tabs = await list_chrome_tabs(self._port)
        page_tabs = [t for t in tabs if t.tab_type == "page"]

        if not page_tabs:
            # No tabs — create one via CDP /json/new endpoint
            logger.info("Chrome has no tabs, creating a new one")
            page_tabs = await self._create_tab()
            if not page_tabs:
                raise ConnectionError("Chrome has no open tabs and failed to create one.")

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

    async def _auto_launch_chrome(self) -> str | None:
        """Launch headless browser with remote debugging. Returns CDP ws_url or None.

        Auto-detects Brave > Chrome > Chromium. Uses the shared profile
        directory (same as Playwright) so cookies persist between both engines.
        Kills stale processes on the debugging port before launching.
        """
        import os

        from lazyclaw.config import load_config

        config = load_config()
        chrome_bin = config.browser_executable

        if not chrome_bin:
            logger.warning("No browser found (Brave/Chrome/Chromium), cannot auto-launch")
            return None

        # Kill any stale headless browser hogging the debugging port
        try:
            kill_proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", f"--remote-debugging-port={self._port}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
            await asyncio.sleep(0.5)
        except Exception:
            pass

        # Use shared profile dir (same as Playwright) for cookie sharing,
        # or fall back to a persistent temp dir
        if self._profile_dir:
            profile_dir = self._profile_dir
            os.makedirs(profile_dir, exist_ok=True)
        else:
            import tempfile
            profile_dir = os.path.join(tempfile.gettempdir(), "lazyclaw-cdp-profile")

        cmd = [
            chrome_bin,
            "--headless=new",
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            logger.info(
                "Launched headless Chrome (pid=%d, port=%d, profile=%s)",
                proc.pid, self._port, profile_dir,
            )
            # Wait for Chrome to start accepting connections
            for _ in range(15):
                await asyncio.sleep(0.5)
                ws_url = await find_chrome_cdp(self._port)
                if ws_url:
                    return ws_url
            logger.warning("Chrome launched but CDP not responding after 7.5s")
        except Exception as exc:
            logger.error("Failed to launch Chrome: %s", exc)

        return None

    async def _create_tab(self, url: str = "about:blank") -> list[CDPTab]:
        """Create a new tab via Chrome's /json/new endpoint."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.put(
                    f"http://localhost:{self._port}/json/new?{url}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tab = CDPTab(
                        id=data.get("id", ""),
                        title=data.get("title", ""),
                        url=data.get("url", url),
                        ws_url=data.get("webSocketDebuggerUrl", ""),
                        tab_type=data.get("type", "page"),
                    )
                    if tab.ws_url:
                        logger.info("Created new tab: %s", tab.url)
                        return [tab]
        except Exception as exc:
            logger.warning("Failed to create tab: %s", exc)
        return []

    async def goto(self, url: str) -> None:
        conn = await self._ensure_connected()
        await conn.send("Page.navigate", {"url": url})
        # Human-like wait for page load (0.8-1.5s)
        await asyncio.sleep(random.uniform(0.8, 1.5))
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
        # Small delay between press and release (human finger)
        await conn.send("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": x, "y": y,
            "button": "left",
            "clickCount": 1,
        })
        await asyncio.sleep(random.uniform(0.05, 0.12))
        await conn.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": x, "y": y,
            "button": "left",
            "clickCount": 1,
        })
        # Human pause after clicking (0.2-1.5s)
        await asyncio.sleep(random.uniform(0.2, 1.5))

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
        await asyncio.sleep(random.uniform(0.1, 0.3))
        # Type each character with human-like timing
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
            await asyncio.sleep(random.uniform(0.03, 0.12))  # Human typing speed

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

    # ── Accessibility tree ────────────────────────────────────────────

    async def accessibility_tree(self, max_depth: int = 8) -> str:
        """Get the page's accessibility tree as compact text.

        Returns semantic roles, names, and states — works on ANY site
        without custom JS extractors. Typically 2-5KB vs 50KB raw text.
        """
        conn = await self._ensure_connected()
        await conn.send("Accessibility.enable")
        result = await conn.send("Accessibility.getFullAXTree", {"depth": max_depth})
        nodes = result.get("nodes", [])

        lines: list[str] = []
        node_map = {n["nodeId"]: n for n in nodes}

        def _format_node(node: dict, depth: int = 0) -> None:
            role = node.get("role", {}).get("value", "")
            name = node.get("name", {}).get("value", "")
            props = node.get("properties", [])

            # Skip ignored/invisible nodes
            if role in ("none", "generic", "InlineTextBox", "LineBreak"):
                for cid in node.get("childIds", []):
                    child = node_map.get(cid)
                    if child:
                        _format_node(child, depth)
                return

            # Build compact line
            parts = [role]
            if name:
                parts.append(f'"{name}"')
            for prop in props:
                pname = prop.get("name", "")
                pval = prop.get("value", {}).get("value", "")
                if pname in ("checked", "selected", "disabled", "expanded",
                             "pressed", "required") and pval:
                    parts.append(f"{pname}={pval}")
                elif pname == "value" and pval:
                    parts.append(f'value="{str(pval)[:60]}"')

            line = "  " * depth + " ".join(parts)
            if line.strip():
                lines.append(line)

            for cid in node.get("childIds", []):
                child = node_map.get(cid)
                if child:
                    _format_node(child, depth + 1)

        # Start from root
        if nodes:
            _format_node(nodes[0], 0)

        return "\n".join(lines)

    async def find_element_by_role(self, description: str) -> dict | None:
        """Find an element by role/label description and return its coordinates.

        Accepts natural descriptions like "Search input", "Submit button",
        "Send message". Returns {"x": float, "y": float, "nodeId": str} or None.
        """
        conn = await self._ensure_connected()
        await conn.send("Accessibility.enable")
        result = await conn.send("Accessibility.getFullAXTree", {"depth": 6})
        nodes = result.get("nodes", [])

        desc_lower = description.lower()
        best_match = None
        best_score = 0

        for node in nodes:
            role = node.get("role", {}).get("value", "").lower()
            name = node.get("name", {}).get("value", "").lower()
            backend_id = node.get("backendDOMNodeId")
            if not backend_id:
                continue

            # Score how well this node matches the description
            score = 0
            for word in desc_lower.split():
                if word in role:
                    score += 2
                if word in name:
                    score += 3

            if score > best_score:
                best_score = score
                best_match = (node, backend_id)

        if not best_match or best_score < 2:
            return None

        node, backend_id = best_match

        # Resolve to DOM node and get coordinates
        try:
            dom_result = await conn.send(
                "DOM.describeNode", {"backendNodeId": backend_id}
            )
            node_id_result = await conn.send(
                "DOM.requestNode", {"backendNodeId": backend_id}
            )
            dom_node_id = node_id_result.get("nodeId")
            if not dom_node_id:
                return None

            box = await conn.send(
                "DOM.getBoxModel", {"nodeId": dom_node_id}
            )
            content = box.get("model", {}).get("content", [])
            if len(content) >= 4:
                # content is [x1,y1, x2,y2, x3,y3, x4,y4] — use center
                x = (content[0] + content[2]) / 2
                y = (content[1] + content[5]) / 2
                return {
                    "x": x, "y": y,
                    "nodeId": str(backend_id),
                    "role": node.get("role", {}).get("value", ""),
                    "name": node.get("name", {}).get("value", ""),
                }
        except Exception as exc:
            logger.debug("find_element_by_role coordinate lookup failed: %s", exc)

        return None

    # ── Console logs ────────────────────────────────────────────────

    async def enable_console(self) -> None:
        """Enable console log collection."""
        conn = await self._ensure_connected()
        self._console_logs: list[dict] = getattr(self, "_console_logs", [])
        await conn.send("Console.enable")

    async def get_console_logs(self, clear: bool = True) -> list[dict]:
        """Get collected console logs. Returns list of {level, text, url, line}."""
        conn = await self._ensure_connected()

        # Fetch via Runtime.evaluate since Console events need listener setup
        result = await conn.send("Runtime.evaluate", {
            "expression": """(() => {
                const logs = window.__lazyclaw_console_logs || [];
                return JSON.stringify(logs.slice(-50));
            })()""",
            "returnByValue": True,
        })
        raw = result.get("result", {}).get("value", "[]")

        import json
        try:
            logs = json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            logs = []

        if clear:
            await conn.send("Runtime.evaluate", {
                "expression": "window.__lazyclaw_console_logs = [];",
            })

        return logs

    async def inject_console_capture(self) -> None:
        """Inject JS to capture console.log/warn/error into a buffer."""
        conn = await self._ensure_connected()
        await conn.send("Runtime.evaluate", {
            "expression": """(() => {
                if (window.__lazyclaw_console_hooked) return;
                window.__lazyclaw_console_logs = [];
                const orig = {log: console.log, warn: console.warn, error: console.error, info: console.info};
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
            })()""",
        })

    # ── Hover and drag ──────────────────────────────────────────────

    async def hover(self, selector: str) -> None:
        """Hover over an element matching the CSS selector."""
        conn = await self._ensure_connected()
        js = f"""
        (() => {{
            const el = document.querySelector({_js_str(selector)});
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}};
        }})()
        """
        result = await conn.send(
            "Runtime.evaluate", {"expression": js, "returnByValue": True}
        )
        coords = result.get("result", {}).get("value")
        if not coords:
            raise ValueError(f"Element not found: {selector}")

        await conn.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": coords["x"], "y": coords["y"],
        })
        await asyncio.sleep(random.uniform(0.3, 0.8))

    async def drag_and_drop(
        self, source_selector: str, target_selector: str
    ) -> None:
        """Drag element from source to target."""
        conn = await self._ensure_connected()
        js = f"""
        (() => {{
            const src = document.querySelector({_js_str(source_selector)});
            const tgt = document.querySelector({_js_str(target_selector)});
            if (!src || !tgt) return null;
            const sr = src.getBoundingClientRect();
            const tr = tgt.getBoundingClientRect();
            return {{
                sx: sr.x + sr.width / 2, sy: sr.y + sr.height / 2,
                tx: tr.x + tr.width / 2, ty: tr.y + tr.height / 2
            }};
        }})()
        """
        result = await conn.send(
            "Runtime.evaluate", {"expression": js, "returnByValue": True}
        )
        coords = result.get("result", {}).get("value")
        if not coords:
            raise ValueError("Source or target element not found")

        sx, sy, tx, ty = coords["sx"], coords["sy"], coords["tx"], coords["ty"]

        await conn.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": sx, "y": sy,
            "button": "left", "clickCount": 1,
        })
        await asyncio.sleep(random.uniform(0.1, 0.2))

        # Move in steps for natural drag
        steps = 5
        for i in range(1, steps + 1):
            frac = i / steps
            await conn.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": sx + (tx - sx) * frac,
                "y": sy + (ty - sy) * frac,
            })
            await asyncio.sleep(random.uniform(0.03, 0.08))

        await conn.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": tx, "y": ty,
            "button": "left", "clickCount": 1,
        })
        await asyncio.sleep(random.uniform(0.2, 0.5))

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


async def restart_browser_with_cdp(
    port: int = 9222,
    profile_dir: str | None = None,
    browser_bin: str | None = None,
) -> str | None:
    """Kill running Brave/Chrome and relaunch with CDP enabled (visible).

    Same profile directory → all tabs, cookies, sessions preserved.
    Returns CDP ws_url or None.
    """
    import os

    if not browser_bin:
        from lazyclaw.config import load_config
        config = load_config()
        browser_bin = config.browser_executable

    if not browser_bin:
        logger.warning("No browser binary found")
        return None

    # Kill ALL Brave/Chrome instances (visible + headless)
    browser_name = os.path.basename(browser_bin).lower()
    kill_patterns = [
        f"--remote-debugging-port={port}",  # headless with CDP
    ]
    # Also kill the main browser process
    if "brave" in browser_name:
        kill_patterns.append("Brave Browser")
    else:
        kill_patterns.append("Google Chrome")

    for pattern in kill_patterns:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", pattern,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            pass

    await asyncio.sleep(1.5)  # Wait for graceful shutdown

    # Clean stale profile locks
    if profile_dir:
        os.makedirs(profile_dir, exist_ok=True)
        for lock_file in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lock_path = os.path.join(profile_dir, lock_file)
            try:
                if os.path.exists(lock_path) or os.path.islink(lock_path):
                    os.unlink(lock_path)
            except OSError:
                pass

    # Relaunch VISIBLE browser with CDP
    cmd = [
        browser_bin,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--disable-blink-features=AutomationControlled",
    ]
    if profile_dir:
        cmd.append(f"--user-data-dir={profile_dir}")

    try:
        await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info(
            "Relaunched browser with CDP (port=%d, profile=%s)",
            port, profile_dir,
        )

        # Wait for CDP to respond (up to 10s)
        for _ in range(20):
            await asyncio.sleep(0.5)
            ws_url = await find_chrome_cdp(port)
            if ws_url:
                return ws_url

        logger.warning("Browser launched but CDP not responding after 10s")
    except Exception as exc:
        logger.error("Failed to relaunch browser: %s", exc)

    return None


def _js_str(s: str) -> str:
    """Escape a Python string for safe use in JavaScript."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
