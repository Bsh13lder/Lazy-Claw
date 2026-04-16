"""CDP browser backend — controls user's real Chrome/Brave browser.

Uses Chrome DevTools Protocol via ws://localhost:9222 (configurable port).
Standalone module — no ABC or Playwright dependency.

Utility functions (restart_browser_with_cdp, js_str, is_same_origin_nav)
live in cdp_utils.py and are re-exported here for backwards compatibility.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lazyclaw.browser.cdp import (
    CDPConnection,
    CDPTab,
    find_chrome_cdp,
    list_chrome_tabs,
)
from lazyclaw.browser.cdp_utils import (
    is_same_origin_nav as _is_same_origin_nav,
    js_str as _js_str,
    restart_browser_with_cdp,
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

    def __init__(
        self,
        port: int = 9222,
        profile_dir: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._port = port
        self._profile_dir = profile_dir  # Shared with Playwright when set
        self._user_id = user_id           # When set, action events are published
        self._conn: CDPConnection | None = None
        self._current_tab: CDPTab | None = None
        self._last_activity: float = 0.0
        self._connect_lock = asyncio.Lock()
        self._last_thumb_url: str | None = None  # Throttle thumb captures per URL change

    def set_user_id(self, user_id: str | None) -> None:
        """Late-bind user_id (used by the shared singleton when user switches)."""
        self._user_id = user_id

    def _emit(
        self,
        kind: str,
        action: str | None = None,
        target: str | None = None,
        url: str | None = None,
        title: str | None = None,
        detail: str | None = None,
        extra: dict | None = None,
    ) -> None:
        """Publish a browser event. No-op if no user_id is bound."""
        if not self._user_id:
            return
        try:
            from lazyclaw.browser.event_bus import BrowserEvent, is_live_mode, publish
            publish(BrowserEvent(
                user_id=self._user_id,
                kind=kind,
                action=action,
                target=target,
                url=url,
                title=title,
                detail=detail,
                extra=extra,
            ))
            # In live mode, schedule a fresh thumbnail after every action so
            # the canvas reflects what the agent actually sees, not a stale frame.
            if kind == "action" and is_live_mode(self._user_id):
                try:
                    asyncio.get_running_loop().create_task(self._maybe_live_capture())
                except RuntimeError:
                    # No running loop (sync context) — safe to skip
                    pass
        except Exception:
            logger.debug("Browser event publish failed (non-fatal)", exc_info=True)

    async def _capture_thumbnail(self, url: str | None, force: bool = False) -> None:
        """Capture a small WebP thumbnail and store it in the event bus.

        Throttled: only captures when URL changes (or when force=True / live mode).
        Uses Pillow if available, else stores the raw PNG.
        """
        if not self._user_id or not self._conn:
            return
        if not force and url and url == self._last_thumb_url:
            return
        try:
            # Use the inline path (don't call self.screenshot to avoid emitting
            # a fake "screenshot" action event for the agent log).
            params: dict = {"format": "png", "quality": 80}
            result = await self._conn.send("Page.captureScreenshot", params)
            png_bytes = base64.b64decode(result.get("data", ""))
            if not png_bytes:
                return
            thumb_bytes = png_bytes
            try:
                import io
                from PIL import Image
                img = Image.open(io.BytesIO(png_bytes))
                if img.width > 640:
                    ratio = 640.0 / img.width
                    img = img.resize((640, int(img.height * ratio)))
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=70)
                thumb_bytes = buf.getvalue()
            except Exception:
                logger.debug("Pillow downscale unavailable, storing PNG as-is", exc_info=True)
            from lazyclaw.browser.event_bus import set_thumbnail
            set_thumbnail(self._user_id, thumb_bytes, url=url)
            self._last_thumb_url = url
        except Exception:
            logger.debug("Thumbnail capture failed", exc_info=True)

    async def _maybe_live_capture(self) -> None:
        """If live mode is on, capture a thumbnail right now.

        Called after every user-visible action when the user is actively
        watching the canvas. No-op when live mode is off (zero overhead).
        """
        if not self._user_id:
            return
        try:
            from lazyclaw.browser.event_bus import is_live_mode
            if not is_live_mode(self._user_id):
                return
        except Exception:
            return
        try:
            url = await self.current_url()
        except Exception:
            url = None
        # Force capture even if URL is unchanged — that's the whole point of live mode.
        asyncio.create_task(self._capture_thumbnail(url, force=True))

    async def _ensure_connected(self) -> CDPConnection:
        """Lazy connect: discover Chrome and connect to active tab.

        If no Chrome is running, auto-launches headless Chrome.
        If Chrome has no tabs, creates a new blank tab automatically.
        Uses asyncio.Lock to prevent duplicate Chrome launches from
        concurrent coroutines.
        """
        # Fast path: already connected (no lock needed)
        if self._conn and self._conn.is_connected:
            self._last_activity = time.monotonic()
            return self._conn

        async with self._connect_lock:
            # Re-check after acquiring lock (another coroutine may have connected)
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

            # Apply stealth (mobile UA, anti-detection, touch emulation)
            try:
                from lazyclaw.browser.stealth import apply_stealth
                await apply_stealth(self._conn)
            except Exception as exc:
                logger.warning("Stealth injection failed (non-fatal): %s", exc)

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
            logger.warning("Failed to kill existing browser process on port %d", self._port, exc_info=True)

        # Use shared profile dir (same as Playwright) for cookie sharing,
        # or fall back to a persistent temp dir
        if self._profile_dir:
            profile_dir = self._profile_dir
            os.makedirs(profile_dir, exist_ok=True)
        else:
            import tempfile
            profile_dir = os.path.join(tempfile.gettempdir(), "lazyclaw-cdp-profile")

        # Clean stale profile locks (from crashed/killed containers or processes)
        for lock_file in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lock_path = os.path.join(profile_dir, lock_file)
            try:
                if os.path.exists(lock_path) or os.path.islink(lock_path):
                    os.unlink(lock_path)
            except OSError:
                logger.debug("Failed to remove stale browser lock file: %s", lock_path, exc_info=True)

        # Auto-load LazyClaw ref engine extension (silent, no user prompt)
        ext_path = str(Path(__file__).parent / "extension")

        from lazyclaw.browser.stealth import STEALTH_LAUNCH_ARGS

        cmd = [
            chrome_bin,
            "--headless=new",
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            *STEALTH_LAUNCH_ARGS,
            f"--load-extension={ext_path}",
            f"--disable-extensions-except={ext_path}",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info(
                "Launched headless Chrome (pid=%d, port=%d, profile=%s)",
                proc.pid, self._port, profile_dir,
            )
            # Wait for Chrome to start accepting connections
            for i in range(6):
                await asyncio.sleep(0.5)
                # Check if process died (zombie reaping)
                if proc.returncode is not None:
                    stderr_bytes = await proc.stderr.read() if proc.stderr else b""
                    stderr_tail = stderr_bytes[-500:].decode("utf-8", errors="replace")
                    logger.error(
                        "Chrome exited with code %d after %.1fs. stderr: %s",
                        proc.returncode, (i + 1) * 0.5, stderr_tail,
                    )
                    break
                ws_url = await find_chrome_cdp(self._port)
                if ws_url:
                    return ws_url
            else:
                logger.warning("Chrome launched but CDP not responding after 3s")
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

        self._emit(
            kind="navigate", action="goto", url=url,
            detail=f"Going to {url}",
        )

        # SPA hash navigation: if same origin but different hash/path,
        # Page.navigate won't work (Gmail, Twitter, etc. ignore it).
        # Use JS window.location.href instead, which SPAs do handle.
        current = await self.current_url()
        if _is_same_origin_nav(current, url):
            logger.info("SPA navigation detected: %s → %s", current[:60], url[:60])
            await conn.send(
                "Runtime.evaluate",
                {
                    "expression": f"window.location.href = {_js_str(url)};",
                    "awaitPromise": False,
                },
            )
            # SPAs need time to render after hash change (Gmail: 1-2s)
            await asyncio.sleep(random.uniform(1.5, 2.5))
            await self._post_nav_emit(url)
            return

        await conn.send("Page.navigate", {"url": url})
        # Human-like wait for page load (0.8-1.5s)
        await asyncio.sleep(random.uniform(0.8, 1.5))
        try:
            await conn.send(
                "Page.waitForNavigation",
                {"timeout": 10000},
            )
        except Exception:
            logger.debug("Page.waitForNavigation not triggered (expected for some pages)", exc_info=True)

        # Wait for DOM to settle + auto-solve Cloudflare if detected
        try:
            from lazyclaw.browser.stealth import detect_and_solve_cloudflare, wait_for_page_ready

            await wait_for_page_ready(conn, timeout=5.0)
            await detect_and_solve_cloudflare(conn, timeout=20.0)
        except Exception:
            logger.warning("Page ready / Cloudflare detection failed after navigation", exc_info=True)

        await self._post_nav_emit(url)

    async def _post_nav_emit(self, requested_url: str) -> None:
        """Emit navigate event with final URL+title and capture a thumbnail."""
        if not self._user_id:
            return
        try:
            final_url = await self.current_url()
        except Exception:
            final_url = requested_url
        try:
            page_title = await self.title()
        except Exception:
            page_title = None
        self._emit(
            kind="navigate", action="goto", url=final_url, title=page_title,
            detail=f"Loaded {page_title or final_url}",
        )
        # Fire-and-forget thumbnail (don't block the agent loop)
        asyncio.create_task(self._capture_thumbnail(final_url))

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
        png_bytes = base64.b64decode(b64_data)
        # Update thumbnail cache opportunistically. Don't recurse — the
        # thumbnail helper calls screenshot() too, but with throttling.
        if self._user_id and not full_page:
            try:
                from lazyclaw.browser.event_bus import set_thumbnail
                try:
                    cur_url = await self.current_url()
                except Exception:
                    cur_url = None
                # Cheap downscale path inline to avoid circular call
                try:
                    import io
                    from PIL import Image
                    img = Image.open(io.BytesIO(png_bytes))
                    if img.width > 640:
                        ratio = 640.0 / img.width
                        img = img.resize((640, int(img.height * ratio)))
                    buf = io.BytesIO()
                    img.save(buf, format="WEBP", quality=70)
                    set_thumbnail(self._user_id, buf.getvalue(), url=cur_url)
                except Exception:
                    set_thumbnail(self._user_id, png_bytes, url=cur_url)
            except Exception:
                logger.debug("Thumbnail cache update failed", exc_info=True)
        self._emit(
            kind="action", action="screenshot",
            detail="Captured screenshot",
        )
        return png_bytes

    async def click(self, selector: str) -> None:
        conn = await self._ensure_connected()
        # Find element center coordinates and size via JS
        js = f"""
        (() => {{
            const el = document.querySelector({_js_str(selector)});
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{
                x: rect.x + rect.width / 2,
                y: rect.y + rect.height / 2,
                w: rect.width, h: rect.height
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
        target_size = min(coords.get("w", 20), coords.get("h", 20))

        # Human-like click: Bezier movement + complete event chain
        from lazyclaw.browser.human_input import human_click
        await human_click(conn, x, y, target_size=target_size)

        self._emit(
            kind="action", action="click", target=selector[:80],
            detail=f"Clicked {selector[:60]}",
        )

    async def type_text(self, selector: str, text: str) -> None:
        conn = await self._ensure_connected()
        # Find element coordinates and click to focus (human-like)
        js = f"""
        (() => {{
            const el = document.querySelector({_js_str(selector)});
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}};
        }})()
        """
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True},
        )
        coords = result.get("result", {}).get("value")

        # Type with human-like keystroke timing
        from lazyclaw.browser.human_input import human_type
        await human_type(
            conn, text,
            field_x=coords["x"] if coords else None,
            field_y=coords["y"] if coords else None,
        )

        # Mask passwords/secrets in the visible detail line
        masked = text if len(text) <= 40 else text[:37] + "…"
        if any(s in selector.lower() for s in ("password", "passwd", "pin", "secret")):
            masked = "•" * min(len(text), 8)
        self._emit(
            kind="action", action="type", target=selector[:80],
            detail=f"Typed '{masked}' into {selector[:40]}",
        )

    async def press_key(self, key: str) -> None:
        """Press a keyboard key (Enter, Escape, Tab, Backspace, ArrowDown, etc)."""
        conn = await self._ensure_connected()
        # Map common names to CDP key identifiers
        key_map = {
            "enter": ("Enter", "\r", 13),
            "return": ("Enter", "\r", 13),
            "escape": ("Escape", "", 27),
            "esc": ("Escape", "", 27),
            "tab": ("Tab", "\t", 9),
            "backspace": ("Backspace", "", 8),
            "delete": ("Delete", "", 46),
            "arrowup": ("ArrowUp", "", 38),
            "arrowdown": ("ArrowDown", "", 40),
            "arrowleft": ("ArrowLeft", "", 37),
            "arrowright": ("ArrowRight", "", 39),
            "space": (" ", " ", 32),
        }
        lookup = key_map.get(key.lower().replace(" ", ""))
        if lookup:
            key_name, text, code = lookup
        else:
            # Single printable char → send as text; multi-char names (e.g.
            # "F5", "PageDown") are key identifiers, not literal text.
            key_name = key
            text = key if len(key) == 1 else ""
            code = ord(key.upper()) if len(key) == 1 else 0

        key_down = {
            "type": "keyDown",
            "key": key_name,
            "windowsVirtualKeyCode": code,
            "nativeVirtualKeyCode": code,
        }
        # CDP rejects text="" — only include when non-empty
        if text:
            key_down["text"] = text
        await conn.send("Input.dispatchKeyEvent", key_down)
        await asyncio.sleep(random.uniform(0.03, 0.08))
        await conn.send("Input.dispatchKeyEvent", {
            "type": "keyUp",
            "key": key_name,
            "windowsVirtualKeyCode": code,
            "nativeVirtualKeyCode": code,
        })
        self._emit(
            kind="action", action="press_key", target=key,
            detail=f"Pressed {key}",
        )

    async def scroll(self, direction: str = "down", amount: int = 300) -> None:
        conn = await self._ensure_connected()
        # Human-like scroll with momentum deceleration
        from lazyclaw.browser.human_input import human_scroll
        await human_scroll(conn, direction=direction, amount=amount)
        self._emit(
            kind="action", action="scroll", target=direction,
            detail=f"Scrolled {direction} {amount}px",
        )

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
        "Send message". Returns {"x": float, "y": float, "role": str, "name": str} or None.
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

        # Use JS to resolve backendNodeId to coordinates (more reliable than DOM.getBoxModel)
        try:
            resolve_result = await conn.send(
                "DOM.resolveNode", {"backendNodeId": backend_id}
            )
            object_id = resolve_result.get("object", {}).get("objectId")
            if not object_id:
                return None

            # Call getBoundingClientRect() on the resolved object
            box_result = await conn.send("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": """function() {
                    const rect = this.getBoundingClientRect();
                    return {x: rect.x + rect.width/2, y: rect.y + rect.height/2,
                            w: rect.width, h: rect.height};
                }""",
                "returnByValue": True,
            })
            coords = box_result.get("result", {}).get("value")
            if coords and coords.get("w", 0) > 0:
                return {
                    "x": coords["x"], "y": coords["y"],
                    "role": node.get("role", {}).get("value", ""),
                    "name": node.get("name", {}).get("value", ""),
                }
        except Exception as exc:
            logger.debug("find_element_by_role coordinate lookup failed: %s", exc)

        return None

    async def click_by_role(self, description: str) -> dict | None:
        """Find element by role/label and click it via DOM click().

        Uses the same accessibility tree search as find_element_by_role,
        but dispatches mousedown + mouseup + click on the actual DOM element.
        Returns {"role": str, "name": str} if clicked, None if not found.
        """
        # Emit will fire after success below — record intent here too so
        # the user sees the agent attempting it even if it fails.
        self._emit(
            kind="action", action="click_by_role", target=description[:80],
            detail=f"Looking for {description!r}",
        )
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
        try:
            resolve_result = await conn.send(
                "DOM.resolveNode", {"backendNodeId": backend_id}
            )
            object_id = resolve_result.get("object", {}).get("objectId")
            if not object_id:
                return None

            await conn.send("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": """function() {
                    this.scrollIntoView({block: 'center', behavior: 'instant'});
                    this.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                    this.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                    this.click();
                }""",
            })
            role_name = node.get("role", {}).get("value", "")
            label = node.get("name", {}).get("value", "")
            self._emit(
                kind="action", action="click", target=label or role_name,
                detail=f"Clicked {role_name} '{label}'" if label else f"Clicked {role_name}",
            )
            return {"role": role_name, "name": label}
        except Exception as exc:
            logger.debug("click_by_role failed: %s", exc)

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
            logger.debug("Failed to parse console logs JSON", exc_info=True)
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

        # Human-like Bezier movement to hover position
        from lazyclaw.browser.human_input import human_move_to
        await human_move_to(conn, coords["x"], coords["y"])
        await asyncio.sleep(random.uniform(0.3, 0.8))

    async def drag_and_drop(
        self, source_selector: str, target_selector: str
    ) -> None:
        """Drag element from source to target with Bezier path."""
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

        # Move to source with Bezier curve
        from lazyclaw.browser.human_input import human_move_to, _generate_bezier_path
        await human_move_to(conn, sx, sy)

        # Press and hold
        await conn.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": round(sx), "y": round(sy),
            "button": "left", "clickCount": 1,
        })
        await asyncio.sleep(random.uniform(0.1, 0.2))

        # Drag along Bezier curve to target
        path = _generate_bezier_path(sx, sy, tx, ty)
        for point in path:
            await conn.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": round(point.x),
                "y": round(point.y),
            })
            await asyncio.sleep(random.uniform(0.02, 0.06))

        # Release at target
        await conn.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": round(tx), "y": round(ty),
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

    # ------------------------------------------------------------------
    # Tab management via CDP Target domain (for TabManager)
    # ------------------------------------------------------------------

    async def new_tab(self, url: str = "about:blank") -> str:
        """Create a new browser tab via CDP Target domain. Returns targetId."""
        conn = await self._ensure_connected()
        result = await conn.send("Target.createTarget", {"url": url})
        target_id = result.get("targetId", "")
        if not target_id:
            raise RuntimeError("Target.createTarget returned no targetId")
        return target_id

    async def attach_to_target(self, target_id: str) -> str:
        """Attach to a target with flat session mode. Returns sessionId.

        Flat mode multiplexes all tab sessions over the single WebSocket
        connection, using sessionId to route commands to the correct tab.
        """
        conn = await self._ensure_connected()
        result = await conn.send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        session_id = result.get("sessionId", "")
        if not session_id:
            raise RuntimeError("Target.attachToTarget returned no sessionId")
        # Enable required CDP domains in the new session
        await conn.send("Page.enable", session_id=session_id)
        await conn.send("Runtime.enable", session_id=session_id)
        return session_id

    async def send_to_target(
        self, session_id: str, method: str, params: dict | None = None,
    ) -> dict:
        """Send a CDP command scoped to a specific session (tab)."""
        conn = await self._ensure_connected()
        return await conn.send(method, params, session_id=session_id)

    async def close_tab(self, target_id: str) -> None:
        """Close a specific browser tab via CDP."""
        try:
            conn = await self._ensure_connected()
            await conn.send("Target.closeTarget", {"targetId": target_id})
            self._emit(
                kind="action", action="close_tab", target=target_id[:24],
                detail="Closed tab",
            )
        except Exception as exc:
            logger.debug("close_tab %s failed: %s", target_id, exc)

    @property
    def backend_type(self) -> str:
        return "cdp"


# restart_browser_with_cdp, _is_same_origin_nav, _js_str are imported
# from cdp_utils.py at the top of this file for backwards compatibility.
