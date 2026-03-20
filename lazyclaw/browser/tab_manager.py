"""TabManager — CDP session-scoped tab isolation for parallel specialists.

Each specialist gets its own browser tab via Target.attachToTarget with
flat session multiplexing. All tabs share a single WebSocket connection
to the browser, differentiated by sessionId.

TabContext: scoped operations for one tab (goto, evaluate, click, etc.)
TabLease: tracks tab ownership (who holds it, idle/busy state)
TabManager: coordinates tab lifecycle across concurrent specialists
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from lazyclaw.browser.cdp_backend import CDPBackend

logger = logging.getLogger(__name__)


def _js_str(s: str) -> str:
    """Escape a Python string for safe use in JavaScript."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _extract_domain(url: str) -> str:
    """Extract netloc from URL for tab keying."""
    parsed = urlparse(url)
    return parsed.netloc or url


# ---------------------------------------------------------------------------
# TabContext — scoped browser operations for one CDP target
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TabContext:
    """Scoped browser operations bound to one CDP target session.

    All methods route through backend.send_to_target() with this tab's
    session_id, so they NEVER affect other tabs.
    """

    backend: CDPBackend
    target_id: str
    session_id: str
    domain: str
    specialist_id: str

    async def goto(self, url: str) -> None:
        """Navigate this tab to a URL."""
        await self.backend.send_to_target(
            self.session_id, "Page.navigate", {"url": url},
        )
        await asyncio.sleep(random.uniform(0.8, 1.5))

    async def evaluate(self, js: str) -> Any:
        """Run JavaScript in this tab and return the result."""
        result = await self.backend.send_to_target(
            self.session_id,
            "Runtime.evaluate",
            {"expression": js, "returnByValue": True, "awaitPromise": True},
        )
        inner = result.get("result", {})
        if inner.get("type") == "undefined":
            return None
        return inner.get("value", inner.get("description", ""))

    async def title(self) -> str:
        """Get this tab's document title."""
        return (await self.evaluate("document.title")) or ""

    async def current_url(self) -> str:
        """Get this tab's current URL."""
        return (await self.evaluate("window.location.href")) or ""

    async def screenshot(self) -> bytes:
        """Capture a PNG screenshot of this tab."""
        result = await self.backend.send_to_target(
            self.session_id,
            "Page.captureScreenshot",
            {"format": "png", "quality": 80},
        )
        return base64.b64decode(result.get("data", ""))

    async def click(self, selector: str) -> None:
        """Click an element by CSS selector in this tab."""
        js = (
            "(() => {"
            f" const el = document.querySelector({_js_str(selector)});"
            " if (!el) return null;"
            " const r = el.getBoundingClientRect();"
            " return {x: r.x + r.width/2, y: r.y + r.height/2};"
            "})()"
        )
        coords = await self.evaluate(js)
        if not coords:
            raise RuntimeError(f"Element not found: {selector}")
        x, y = coords["x"], coords["y"]
        send = self.backend.send_to_target
        sid = self.session_id
        await send(sid, "Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        await asyncio.sleep(random.uniform(0.05, 0.12))
        await send(sid, "Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        await asyncio.sleep(random.uniform(0.2, 1.5))

    async def type_text(self, selector: str, text: str) -> None:
        """Type text into an element by CSS selector in this tab."""
        await self.evaluate(
            f"document.querySelector({_js_str(selector)})?.focus()"
        )
        await asyncio.sleep(random.uniform(0.1, 0.3))
        send = self.backend.send_to_target
        sid = self.session_id
        for char in text:
            await send(sid, "Input.dispatchKeyEvent", {
                "type": "keyDown", "text": char, "key": char,
            })
            await send(sid, "Input.dispatchKeyEvent", {
                "type": "keyUp", "key": char,
            })
            await asyncio.sleep(random.uniform(0.03, 0.12))

    async def scroll(self, direction: str = "down", amount: int = 300) -> None:
        """Scroll this tab up or down."""
        delta_y = -amount if direction == "up" else amount
        await self.backend.send_to_target(
            self.session_id,
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": 400, "y": 300,
             "deltaX": 0, "deltaY": delta_y},
        )

    async def close(self) -> None:
        """Close this tab and detach the CDP session."""
        await self.backend.close_tab(self.target_id)


# ---------------------------------------------------------------------------
# TabLease — ownership tracker (intentionally mutable)
# ---------------------------------------------------------------------------


@dataclass
class TabLease:
    """Mutable tracker of tab ownership."""

    context: TabContext
    in_use: bool = True
    specialist_id: str = ""
    last_used: float = field(default_factory=time.monotonic)

    def acquire(self, specialist_id: str) -> None:
        self.in_use = True
        self.specialist_id = specialist_id
        self.last_used = time.monotonic()

    def release(self) -> None:
        self.in_use = False
        self.specialist_id = ""
        self.last_used = time.monotonic()


# ---------------------------------------------------------------------------
# TabManager — coordinates tabs across parallel specialists
# ---------------------------------------------------------------------------


class TabManager:
    """Coordinates CDP tabs across parallel specialists.

    Rules:
    - Each specialist gets its own tab (keyed by domain)
    - Idle tab for same domain -> reuse (preserves cookies/state)
    - Tab occupied -> specialist waits via asyncio.Future
    - At max_tabs -> evict oldest idle tab
    - On specialist done -> release tab, wake waiters
    """

    def __init__(self, backend: CDPBackend, max_tabs: int = 5) -> None:
        self._backend = backend
        self._max_tabs = max_tabs
        self._leases: dict[str, TabLease] = {}
        self._waiters: dict[str, list[asyncio.Future[TabContext]]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, url: str, specialist_id: str) -> TabContext:
        """Get an exclusive tab for a URL's domain.

        Reuses idle tab for same domain, waits if occupied, creates new
        if under limit, evicts oldest idle if at capacity.
        """
        domain = _extract_domain(url)
        waiter_fut: asyncio.Future[TabContext] | None = None

        async with self._lock:
            # 1. Idle tab for same domain -> reuse
            lease = self._leases.get(domain)
            if lease and not lease.in_use:
                lease.acquire(specialist_id)
                return lease.context

            # 2. Tab occupied -> create waiter, will wait outside lock
            if lease and lease.in_use:
                waiter_fut = asyncio.get_running_loop().create_future()
                self._waiters.setdefault(domain, []).append(waiter_fut)

        # Wait outside the lock if tab is occupied
        if waiter_fut is not None:
            try:
                context = await asyncio.wait_for(waiter_fut, timeout=60.0)
                # Update lease to reflect actual owner
                async with self._lock:
                    lease = self._leases.get(domain)
                    if lease:
                        lease.acquire(specialist_id)
                return context
            except asyncio.TimeoutError:
                async with self._lock:
                    waiters = self._waiters.get(domain, [])
                    if waiter_fut in waiters:
                        waiters.remove(waiter_fut)
                raise RuntimeError(f"Timeout waiting for tab: {domain}")

        async with self._lock:
            # Re-check: another coroutine may have created this tab while we waited
            lease = self._leases.get(domain)
            if lease and not lease.in_use:
                lease.acquire(specialist_id)
                return lease.context

            # 3. Under limit -> create new tab
            if len(self._leases) < self._max_tabs:
                return await self._create_and_lease(url, domain, specialist_id)

            # 4. At limit -> evict oldest idle tab
            idle = [(d, l) for d, l in self._leases.items() if not l.in_use]
            if idle:
                oldest_domain = min(idle, key=lambda x: x[1].last_used)[0]
                old_lease = self._leases.pop(oldest_domain)
                try:
                    await old_lease.context.close()
                except Exception:
                    pass
                return await self._create_and_lease(url, domain, specialist_id)

            raise RuntimeError(
                f"All {self._max_tabs} tabs occupied, cannot acquire for {domain}"
            )

    async def release(self, domain: str, close: bool = False) -> None:
        """Release a tab. Hands off to next waiter or marks idle."""
        async with self._lock:
            lease = self._leases.get(domain)
            if not lease:
                return

            if close:
                try:
                    await lease.context.close()
                except Exception:
                    pass
                self._leases.pop(domain, None)
                for fut in self._waiters.pop(domain, []):
                    if not fut.done():
                        fut.set_exception(RuntimeError(f"Tab closed: {domain}"))
                return

            # Hand off to next waiter if any
            waiters = self._waiters.get(domain, [])
            if waiters:
                next_fut = waiters.pop(0)
                if not waiters:
                    self._waiters.pop(domain, None)
                lease.acquire("pending")
                if not next_fut.done():
                    next_fut.set_result(lease.context)
                return

            lease.release()

    def get_status(self) -> dict:
        """Snapshot for TUI/Telegram display."""
        return {
            "total_tabs": len(self._leases),
            "max_tabs": self._max_tabs,
            "tabs": {
                d: {
                    "in_use": l.in_use,
                    "specialist": l.specialist_id,
                    "domain": d,
                }
                for d, l in self._leases.items()
            },
            "waiting": {
                d: len(futs)
                for d, futs in self._waiters.items()
                if futs
            },
        }

    async def cleanup(self) -> None:
        """Close all tabs. Called on shutdown."""
        async with self._lock:
            for lease in self._leases.values():
                try:
                    await lease.context.close()
                except Exception:
                    pass
            self._leases.clear()
            for futs in self._waiters.values():
                for fut in futs:
                    if not fut.done():
                        fut.set_exception(RuntimeError("TabManager shutting down"))
            self._waiters.clear()

    async def _create_and_lease(
        self, url: str, domain: str, specialist_id: str,
    ) -> TabContext:
        """Create a new tab and lease it. Must be called with _lock held."""
        target_id = await self._backend.new_tab(url)
        session_id = await self._backend.attach_to_target(target_id)
        context = TabContext(
            backend=self._backend,
            target_id=target_id,
            session_id=session_id,
            domain=domain,
            specialist_id=specialist_id,
        )
        self._leases[domain] = TabLease(
            context=context,
            in_use=True,
            specialist_id=specialist_id,
        )
        return context
