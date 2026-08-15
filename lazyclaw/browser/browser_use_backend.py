"""Browser-Use backend — vendored actor/CDP action layer over the host browser.

Drop-in alternative to :class:`~lazyclaw.browser.cdp_backend.CDPBackend`
(same method surface, selected per-user via ``browser_settings["backend"]``).
Built on the vendored browser-use 0.13 actor API (``lazyclaw/_vendor``,
MIT) speaking native CDP through ``cdp-use`` — no Playwright, no browser-use
Agent/LLM loop, no bubus, no watchdogs.

Design invariants (see docs/ARCHITECTURE_REVIEW_2026-08-15.md §5):

- **Attach, never own.** Connects to an EXISTING browser (the user's signed-in
  host Brave via ``resolve_browser_ws_url``) and ``close()`` only drops the
  WebSocket — it can never kill or restart the browser.
- **Our input layer.** Element *location* uses the actor API; input *execution*
  routes through ``human_input`` (Bezier paths + per-domain cadence), so the
  anti-detect timing profile is identical to the CDP backend.
- **Our event bus.** Every action publishes a :class:`BrowserEvent`; nothing
  from this backend ever enters LLM context.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from lazyclaw.browser.cadence import CadenceProfile, get_cadence
from lazyclaw.browser.cdp_utils import js_str

logger = logging.getLogger(__name__)

# Lazy import flag — set after first successful vendored import
_BROWSER_USE_AVAILABLE: bool | None = None


def is_available() -> bool:
    """Check that the vendored browser-use actor subset imports cleanly."""
    global _BROWSER_USE_AVAILABLE
    if _BROWSER_USE_AVAILABLE is None:
        try:
            from lazyclaw._vendor import ensure_vendor_path
            ensure_vendor_path()
            import browser_use.actor  # noqa: F401
            import cdp_use  # noqa: F401
            _BROWSER_USE_AVAILABLE = True
        except ImportError:
            logger.warning(
                "Vendored browser-use unavailable", exc_info=True
            )
            _BROWSER_USE_AVAILABLE = False
    return _BROWSER_USE_AVAILABLE


@dataclass(frozen=True)
class TabInfo:
    """Immutable info about a browser tab (matches CDPBackend.TabInfo)."""

    id: str
    title: str
    url: str
    active: bool = False


class _SessionConn:
    """Adapts ``cdp_use.CDPClient`` to the ``conn.send(method, params)``
    contract shared by ``human_input``, ``snapshot`` helpers and the raw
    call sites in ``skills/builtin/browser_actions`` (which do
    ``conn = await backend._ensure_connected(); conn.send(...)``).

    Bound to one CDP session (one tab); immutable by construction.
    """

    def __init__(self, client: Any, session_id: str | None) -> None:
        self._client = client
        self._session_id = session_id

    async def send(self, method: str, params: dict | None = None) -> dict:
        return await self._client.send_raw(
            method, params or {}, session_id=self._session_id
        )


class _ActorSession:
    """Minimal duck-typed stand-in for browser-use's ``BrowserSession``.

    The vendored actor classes only touch ``.cdp_client`` and ``.id``
    (``cdp_client_for_node`` exists solely on the LLM element-by-prompt
    path, which LazyClaw never calls). Keeping this surface tiny is what
    lets us exclude BrowserSession, bubus and every watchdog.
    """

    def __init__(self, cdp_client: Any) -> None:
        self.cdp_client = cdp_client
        self.id = f"lazyclaw-{uuid.uuid4().hex[:12]}"

    async def cdp_client_for_node(self, node: Any) -> Any:
        raise NotImplementedError(
            "LLM element-by-prompt path is not used by LazyClaw"
        )


class BrowserUseBackend:
    """Browser control via the vendored browser-use actor API (CDP-native).

    Same interface as CDPBackend — ``goto()``, ``click()``, ``type_text()``,
    ``tabs()`` … Selected per-user in
    ``skills/builtin/browser_actions/backends.py::get_cdp_backend``.
    """

    def __init__(
        self,
        port: int = 9222,
        profile_dir: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._port = port
        self._profile_dir = profile_dir
        self._user_id = user_id
        self._cdp_source: str | None = None
        self._client: Any = None
        self._page: Any = None
        self._target_id: str | None = None
        self._session: _ActorSession | None = None
        self._connect_lock = asyncio.Lock()
        self._current_domain: str | None = None
        self._cadence_overrides: dict[str, dict[str, float]] = {}

    # ── Identity / settings ───────────────────────────────────────────

    def set_user_id(self, user_id: str) -> None:
        """Late-bind the user id so events route correctly (shared singleton)."""
        self._user_id = user_id

    def set_cadence_overrides(self, overrides: dict[str, dict[str, float]]) -> None:
        self._cadence_overrides = dict(overrides or {})

    def _active_cadence(self) -> CadenceProfile:
        return get_cadence(self._current_domain, self._cadence_overrides)

    @property
    def backend_type(self) -> str:
        return "browser_use"

    # ── Connection lifecycle (attach-only) ────────────────────────────

    async def _ensure_connected(self) -> _SessionConn:
        """Attach to the running browser and return a page-bound conn.

        Returns a ``_SessionConn`` so existing raw call sites
        (``conn.send("Input.dispatchKeyEvent", …)``) work unchanged.
        """
        async with self._connect_lock:
            if self._client is None or self._client.ws is None:
                await self._connect()
            if self._page is None:
                await self._bind_page(await self._pick_page_target())
        session_id = await self._page.session_id
        return _SessionConn(self._client, session_id)

    async def _resolve_ws_url(self) -> str | None:
        """Locate the CDP endpoint (host Brave preferred in Docker).

        Reuses ``host_bridge.find_cdp_with_preference`` for discovery, then
        rewrites the browser-reported ws URL onto the host we actually
        reached — Brave always reports ``ws://localhost:...`` which, inside
        a container, silently points at the container itself.
        """
        from lazyclaw.browser.host_bridge import (
            HOST_GATEWAY_HOSTNAME,
            find_cdp_with_preference,
        )

        ws, source = await find_cdp_with_preference(
            port=self._port, prefer_host=True
        )
        self._cdp_source = source
        if not ws:
            return None
        if source == "host":
            import socket

            from lazyclaw.browser.cdp import rewrite_ws_host

            try:
                host_ip = socket.gethostbyname(HOST_GATEWAY_HOSTNAME)
            except OSError as exc:
                logger.warning("Host bridge DNS-resolve failed: %s", exc)
                return None
            return rewrite_ws_host(ws, host=host_ip, port=self._port)
        return ws

    async def _connect(self) -> None:
        if not is_available():
            raise RuntimeError(
                "[dependency_missing] vendored browser-use subset failed to "
                "import — check lazyclaw/_vendor and cdp-use/uuid7/psutil deps"
            )
        ws_url = await self._resolve_ws_url()
        if not ws_url:
            raise RuntimeError(
                f"[dependency_missing] no browser reachable on CDP "
                f"port {self._port} (host or local) — is the host bridge "
                f"running?"
            )
        from cdp_use import CDPClient

        client = CDPClient(ws_url)
        await client.start()
        self._client = client
        self._session = _ActorSession(client)
        logger.info(
            "BrowserUse backend attached to %s (user=%s)", ws_url, self._user_id
        )

    async def _pick_page_target(self) -> str:
        """Pick the first real page tab (no devtools/extension targets)."""
        result = await self._client.send_raw("Target.getTargets")
        pages = [
            t for t in result.get("targetInfos", [])
            if t.get("type") == "page"
            and not t.get("url", "").startswith(
                ("devtools://", "chrome-extension://", "brave://")
            )
        ]
        if not pages:
            created = await self._client.send_raw(
                "Target.createTarget", {"url": "about:blank"}
            )
            return created["targetId"]
        return pages[0]["targetId"]

    async def _bind_page(self, target_id: str) -> None:
        from browser_use.actor import Page

        self._page = Page(self._session, target_id)
        self._target_id = target_id
        try:
            url = await self._page.get_url()
            self._current_domain = urlparse(url).netloc or None
        except Exception:
            logger.debug("Could not resolve URL of bound tab", exc_info=True)

    async def is_connected(self) -> bool:
        return self._client is not None and self._client.ws is not None

    async def close(self) -> None:
        """Drop the CDP WebSocket. NEVER closes or restarts the browser —
        this backend attaches to the user's real signed-in Brave."""
        if self._client is not None:
            try:
                await self._client.stop()
            except Exception:
                logger.debug("CDP client stop failed", exc_info=True)
            self._client = None
        self._page = None
        self._target_id = None
        self._session = None
        logger.info("BrowserUse backend detached")

    # ── Event bus (UI-only, never enters LLM context) ─────────────────

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
            from lazyclaw.browser.event_bus import BrowserEvent, publish

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
        except Exception:
            logger.debug("Browser event publish failed (non-fatal)", exc_info=True)

    # ── Navigation ────────────────────────────────────────────────────

    async def goto(self, url: str) -> None:
        await self._ensure_connected()
        await self._page.goto(url)
        self._current_domain = urlparse(url).netloc or self._current_domain
        from lazyclaw.browser.cadence import sample_ms

        await asyncio.sleep(
            sample_ms(self._active_cadence().dwell_after_load_ms) / 1000.0
        )
        self._emit("action", action="goto", target=url[:120], url=url)

    async def current_url(self) -> str:
        await self._ensure_connected()
        return await self._page.get_url()

    async def title(self) -> str:
        await self._ensure_connected()
        return await self._page.get_title()

    async def content(self) -> str:
        return await self.evaluate("document.documentElement.outerHTML")

    async def evaluate(self, js: str) -> Any:
        """Raw ``Runtime.evaluate`` with by-value return.

        Deliberately bypasses the actor's JS-string rewriting so the
        injected ``window.__lazyclaw`` snapshot contract behaves exactly
        as it does on the CDP backend.
        """
        conn = await self._ensure_connected()
        result = await conn.send(
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True, "awaitPromise": True},
        )
        return result.get("result", {}).get("value")

    # ── Element location (actor) + input execution (human_input) ──────

    async def _locate(self, selector: str) -> dict:
        """Locate an element's center + size via JS (viewport coordinates).

        Returns ``{"x", "y", "w", "h"}`` or raises ValueError — same
        contract and error text as CDPBackend so retry hints stay stable.
        """
        js = f"""
        (() => {{
            const el = document.querySelector({js_str(selector)});
            if (!el) return null;
            el.scrollIntoView({{block: 'center', inline: 'center', behavior: 'instant'}});
            const rect = el.getBoundingClientRect();
            return {{
                x: rect.x + rect.width / 2,
                y: rect.y + rect.height / 2,
                w: rect.width, h: rect.height
            }};
        }})()
        """
        coords = await self.evaluate(js)
        if not coords:
            raise ValueError(f"Element not found: {selector}")
        return coords

    async def click(self, selector: str) -> None:
        conn = await self._ensure_connected()
        coords = await self._locate(selector)
        from lazyclaw.browser.human_input import human_click

        await human_click(
            conn, coords["x"], coords["y"],
            target_size=min(coords.get("w", 20), coords.get("h", 20)),
            cadence=self._active_cadence(),
        )
        self._emit(
            "action", action="click", target=selector[:80],
            detail=f"Clicked {selector[:60]}",
        )

    async def type_text(self, selector: str, text: str) -> None:
        conn = await self._ensure_connected()
        coords = await self._locate(selector)
        from lazyclaw.browser.human_input import human_click, human_type

        cadence = self._active_cadence()
        await human_click(
            conn, coords["x"], coords["y"],
            target_size=min(coords.get("w", 20), coords.get("h", 20)),
            cadence=cadence,
        )
        await human_type(conn, text, cadence=cadence)
        self._emit(
            "action", action="type", target=selector[:80],
            detail=f"Typed {len(text)} chars into {selector[:60]}",
        )

    async def press_key(self, key: str) -> None:
        await self._ensure_connected()
        key_map = {
            "enter": "Enter", "return": "Enter",
            "escape": "Escape", "esc": "Escape",
            "tab": "Tab", "backspace": "Backspace",
            "delete": "Delete", "space": " ",
            "arrowup": "ArrowUp", "arrowdown": "ArrowDown",
            "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
        }
        mapped = key_map.get(key.lower().replace(" ", ""), key)
        await self._page.press(mapped)
        self._emit("action", action="press_key", target=mapped)

    async def scroll(self, direction: str = "down", amount: int = 300) -> None:
        conn = await self._ensure_connected()
        from lazyclaw.browser.human_input import human_scroll

        await human_scroll(
            conn, direction=direction, amount=amount,
            cadence=self._active_cadence(),
        )
        self._emit("action", action="scroll", target=f"{direction} {amount}px")

    async def hover(self, selector: str) -> None:
        conn = await self._ensure_connected()
        coords = await self._locate(selector)
        from lazyclaw.browser.human_input import human_move_to

        await human_move_to(conn, coords["x"], coords["y"])
        self._emit("action", action="hover", target=selector[:80])

    async def drag_and_drop(
        self, source_selector: str, target_selector: str
    ) -> None:
        await self._ensure_connected()
        source = await self._find_element(source_selector)
        target = await self._find_element(target_selector)
        await source.drag_to(target)
        self._emit(
            "action", action="drag",
            target=f"{source_selector[:40]} → {target_selector[:40]}",
        )

    async def _find_element(self, selector: str) -> Any:
        """Resolve a CSS selector to a vendored actor Element."""
        elements = await self._page.get_elements_by_css_selector(selector)
        if not elements:
            raise ValueError(f"Element not found: {selector}")
        return elements[0]

    # ── Role-based lookup (compact fuzzy matcher) ─────────────────────

    _ROLE_QUERY_JS = """
    ((desc) => {
        const sels = 'a, button, input, select, textarea, [role], [onclick], [tabindex]';
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        const want = norm(desc);
        if (!want) return null;
        let best = null, bestScore = 0;
        for (const el of document.querySelectorAll(sels)) {
            const rect = el.getBoundingClientRect();
            if (rect.width < 2 || rect.height < 2) continue;
            const name = norm(
                el.getAttribute('aria-label') || el.innerText ||
                el.value || el.placeholder || el.title || ''
            ).slice(0, 120);
            if (!name) continue;
            let score = 0;
            if (name === want) score = 1.0;
            else if (name.includes(want) || want.includes(name)) {
                score = Math.min(name.length, want.length) /
                        Math.max(name.length, want.length);
            }
            if (score > bestScore) {
                bestScore = score;
                best = {
                    x: rect.x + rect.width / 2, y: rect.y + rect.height / 2,
                    w: rect.width, h: rect.height,
                    role: el.getAttribute('role') || el.tagName.toLowerCase(),
                    name: name,
                };
            }
        }
        return bestScore >= 0.3 ? best : null;
    })
    """

    async def find_element_by_role(self, description: str) -> dict | None:
        """Find an interactive element by accessible-name fuzzy match."""
        await self._ensure_connected()
        js = f"({self._ROLE_QUERY_JS})({js_str(description)})"
        try:
            return await self.evaluate(js)
        except Exception as exc:
            logger.debug("find_element_by_role failed: %s", exc)
            return None

    async def click_by_role(self, description: str) -> dict | None:
        conn = await self._ensure_connected()
        match = await self.find_element_by_role(description)
        if not match:
            return None
        from lazyclaw.browser.human_input import human_click

        await human_click(
            conn, match["x"], match["y"],
            target_size=min(match.get("w", 20), match.get("h", 20)),
            cadence=self._active_cadence(),
        )
        self._emit(
            "action", action="click", target=match.get("name", "")[:80],
            detail=f"Clicked by role: {description[:60]}",
        )
        return {"role": match.get("role"), "name": match.get("name")}

    async def wait_for_selector(
        self, selector: str, timeout_ms: int = 5000
    ) -> bool:
        await self._ensure_connected()
        deadline = time.monotonic() + timeout_ms / 1000.0
        probe = f"!!document.querySelector({js_str(selector)})"
        while time.monotonic() < deadline:
            try:
                if await self.evaluate(probe):
                    return True
            except Exception:
                logger.debug("wait_for_selector probe failed", exc_info=True)
            await asyncio.sleep(0.2)
        return False

    # ── Capture ───────────────────────────────────────────────────────

    async def screenshot(self, full_page: bool = False) -> bytes:
        conn = await self._ensure_connected()
        params: dict = {"format": "png"}
        if full_page:
            params["captureBeyondViewport"] = True
        result = await conn.send("Page.captureScreenshot", params)
        self._emit("action", action="screenshot")
        return base64.b64decode(result.get("data", ""))

    # ── Tab management ────────────────────────────────────────────────

    async def tabs(self) -> list[TabInfo]:
        await self._ensure_connected()
        result = await self._client.send_raw("Target.getTargets")
        out = []
        for t in result.get("targetInfos", []):
            if t.get("type") != "page":
                continue
            url = t.get("url", "")
            if url.startswith(("devtools://", "chrome-extension://")):
                continue
            out.append(TabInfo(
                id=t.get("targetId", ""),
                title=t.get("title", ""),
                url=url,
                active=(t.get("targetId") == self._target_id),
            ))
        return out

    async def switch_tab(self, tab_id: str, *, focus: bool = True) -> None:
        async with self._connect_lock:
            if self._client is None or self._client.ws is None:
                await self._connect()
            await self._bind_page(tab_id)
        if focus:
            try:
                await self._client.send_raw(
                    "Target.activateTarget", {"targetId": tab_id}
                )
            except Exception:
                logger.debug("activateTarget failed", exc_info=True)
        self._emit("action", action="switch_tab", target=tab_id)

    async def new_tab(
        self, url: str = "about:blank", *, background: bool = False
    ) -> str:
        async with self._connect_lock:
            if self._client is None or self._client.ws is None:
                await self._connect()
        params: dict = {"url": url}
        if background:
            # Open without stealing the foreground (parity with CDPBackend).
            params["background"] = True
        created = await self._client.send_raw("Target.createTarget", params)
        return created["targetId"]

    async def close_tab(self, target_id: str) -> None:
        await self._ensure_connected()
        await self._client.send_raw(
            "Target.closeTarget", {"targetId": target_id}
        )
        if target_id == self._target_id:
            self._page = None
            self._target_id = None

    # ── Console capture ───────────────────────────────────────────────

    async def inject_console_capture(self) -> None:
        await self.evaluate("""(() => {
            if (window.__lazyclaw_console_hooked) return;
            window.__lazyclaw_console_logs = [];
            const orig = {log: console.log, warn: console.warn, error: console.error};
            for (const [level, fn] of Object.entries(orig)) {
                console[level] = function(...args) {
                    window.__lazyclaw_console_logs.push({
                        level,
                        text: args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' '),
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
        raw = await self.evaluate(
            "JSON.stringify(window.__lazyclaw_console_logs || [])"
        )
        try:
            logs = json.loads(raw) if isinstance(raw, str) else []
        except (json.JSONDecodeError, TypeError):
            logs = []
        if clear:
            await self.evaluate("window.__lazyclaw_console_logs = [];")
        return logs
