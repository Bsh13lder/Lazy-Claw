"""Session acquisition — how `panel_call` authenticates a replayed request.

The whole point of apihunter is to reuse the session the user already
established in their browser. Rather than copy passwords, we read the live
cookies for the target domain straight out of the running browser over CDP
(Chrome DevTools Protocol) and replay them with httpx.

`SessionProvider` is a Protocol so the request layer never depends on a live
browser — unit tests inject a `StaticSessionProvider` with canned cookies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

import httpx


class SessionUnavailable(Exception):
    """Raised when a live session can't be resolved (browser down, not logged
    in). The caller turns this into a ``needs_relogin`` signal for the agent."""


@dataclass(frozen=True)
class ResolvedSession:
    """Cookies (and any CSRF token) for one domain at one moment in time."""

    cookies: dict[str, str] = field(default_factory=dict)
    csrf_token: str | None = None


class SessionProvider(Protocol):
    async def resolve(self, cookie_domain: str) -> ResolvedSession: ...


class StaticSessionProvider:
    """A fixed session — used in tests and for token/manual auth flows."""

    def __init__(self, cookies: dict[str, str], csrf_token: str | None = None) -> None:
        self._session = ResolvedSession(dict(cookies), csrf_token)

    async def resolve(self, cookie_domain: str) -> ResolvedSession:
        return self._session


def _domain_matches(cookie_domain: str, want: str) -> bool:
    """True if a cookie scoped to ``cookie_domain`` applies to host ``want``.

    Mirrors browser scoping: a leading-dot cookie domain matches the host and
    any subdomain; an exact domain matches only that host.
    """
    cd = cookie_domain.lstrip(".").lower()
    want = want.lstrip(".").lower()
    return want == cd or want.endswith("." + cd)


class CdpSessionProvider:
    """Reads live cookies from the running browser via CDP.

    Uses the browser-level DevTools endpoint (`Storage.getCookies`) so no page
    target is required. The websocket URL the browser reports may name
    127.0.0.1/localhost even when we reached it through host.docker.internal,
    so its host:port is rewritten to the endpoint we actually dialed.
    """

    def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    async def resolve(self, cookie_domain: str) -> ResolvedSession:
        ws_url = await self._browser_ws_url()
        cookies = await self._get_cookies(ws_url)
        scoped = {
            c["name"]: c["value"]
            for c in cookies
            if _domain_matches(c.get("domain", ""), cookie_domain)
        }
        if not scoped:
            raise SessionUnavailable(
                f"no cookies found for {cookie_domain!r} — the browser may not "
                "be logged in to this panel"
            )
        return ResolvedSession(cookies=scoped, csrf_token=None)

    async def _browser_ws_url(self) -> str:
        info_url = f"http://{self._host}:{self._port}/json/version"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(info_url)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise SessionUnavailable(
                f"CDP endpoint {self._host}:{self._port} unreachable: {exc}"
            ) from exc
        raw = data.get("webSocketDebuggerUrl")
        if not raw:
            raise SessionUnavailable("CDP endpoint returned no webSocketDebuggerUrl")
        parsed = urlparse(raw)
        # Rewrite host:port to the endpoint we can actually dial.
        return parsed._replace(netloc=f"{self._host}:{self._port}").geturl()

    async def _get_cookies(self, ws_url: str) -> list[dict]:
        # Imported lazily so the package imports even where websockets isn't
        # installed (e.g. a manifest-only unit test run).
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:  # pragma: no cover - depends on install
            raise SessionUnavailable(
                "the 'websockets' package is required for live CDP sessions"
            ) from exc
        try:
            async with connect(ws_url, open_timeout=self._timeout) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies", "params": {}}))
                while True:
                    message = json.loads(await ws.recv())
                    if message.get("id") == 1:
                        if "error" in message:
                            raise SessionUnavailable(
                                f"CDP Storage.getCookies error: {message['error']}"
                            )
                        return message.get("result", {}).get("cookies", [])
        except SessionUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport failure = no session
            raise SessionUnavailable(f"CDP cookie read failed: {exc}") from exc


def cookie_domain_for(base_url: str) -> str:
    """Extract the host of ``base_url`` to use as the default cookie domain."""
    host = urlparse(base_url).hostname
    if not host:
        raise ValueError(f"cannot determine host from base_url {base_url!r}")
    return host
