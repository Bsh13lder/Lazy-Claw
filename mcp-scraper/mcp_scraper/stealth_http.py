"""TLS-fingerprint-impersonating HTTP fetcher.

Plain ``httpx`` exposes a Python TLS handshake (cipher order, ALPN, TLS
extensions) that hardened sites — Cloudflare, Akamai, Imperva, DataDome —
fingerprint and reject before any HTML is served. ``curl_cffi`` rebuilds
the handshake to match a real Chrome / Safari / Firefox build, so we
look identical to a normal browser visit at the network layer.

Why this matters for LazyClaw: today, when crawl4ai's Playwright fetch
fails or we want a cheap text-only fetch, ``fallback_strategies.py``
falls through to plain ``httpx``. On Cloudflare-protected EU business
sites that returns 403 — the agent then tries the expensive Playwright
path AGAIN and times out, and we burn a bunch of time for no data. With
``curl_cffi`` the cheap path actually succeeds.

This module is the drop-in replacement: ``stealth_get(url)`` returns a
``StealthResponse`` whose ``.text``, ``.status_code``, ``.headers``,
``.url`` are the same shape callers expect from ``httpx.Response``.

Optional dep: ``curl_cffi``. When missing, falls back to ``httpx``
silently (one-line debug log) — never hard-fails. Add the dep via
``pip install mcp-scraper[stealth]``.

Approach inspired by Scrapling's ``FetcherSession`` (BSD-3) but the
implementation is independent — TLS impersonation is an IETF concept
(JA3/JA4) and the curl_cffi API is the same regardless of caller.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Browser version → realistic header set. Matches what each impersonate
# mode in curl_cffi sends at the TCP layer, so the headers and the TLS
# fingerprint agree. Keeping these conservative — only the universally
# expected headers; per-site quirks (Sec-Fetch-*, Cookie, etc.) live in
# the caller. Sourced from real Chrome/Safari/Firefox network traces.
_HEADER_PROFILES: dict[str, dict[str, str]] = {
    "chrome": {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    },
    "chrome120": {  # alias — curl_cffi uses chrome120 explicitly
        # Same as chrome above; curl_cffi accepts both names.
    },
    "safari17": {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Safari/605.1.15"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
    },
    "firefox": {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) "
            "Gecko/20100101 Firefox/121.0"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
    },
    "edge": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Microsoft Edge";v="120", "Not_A Brand";v="8", "Chromium";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
    },
}
# Resolve the chrome120 alias once at import.
_HEADER_PROFILES["chrome120"] = dict(_HEADER_PROFILES["chrome"])


def _headers_for(impersonate: str) -> dict[str, str]:
    """Pick the header set matching the impersonate mode. Falls back to
    the chrome profile when an unknown mode is passed (e.g. curl_cffi
    added `chrome131` and we haven't catalogued it yet)."""
    return dict(_HEADER_PROFILES.get(impersonate, _HEADER_PROFILES["chrome"]))


@dataclass
class StealthResponse:
    """httpx.Response-shaped result so callers swap in cleanly.

    Attributes:
        text: Decoded body. Always a str; empty string on failure.
        status_code: HTTP status. 0 when the request never reached the wire.
        headers: Response headers (case-insensitive dict-like).
        url: Final URL after redirects.
        backend: Which path served the request — "curl_cffi" or "httpx".
        error: When status_code is 0, a short human-readable reason.
    """

    text: str = ""
    status_code: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    url: str = ""
    backend: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def raise_for_status(self) -> None:
        """httpx-compatible no-op-if-ok; raises a ValueError otherwise so
        callers' existing `try/except` blocks fire."""
        if not self.ok:
            raise ValueError(
                f"stealth_get failed: status={self.status_code} "
                f"backend={self.backend} error={self.error}"
            )


def _is_curl_cffi_available() -> bool:
    """Tested separately so unit tests can monkey-patch import availability."""
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


async def _fetch_curl_cffi(
    url: str,
    *,
    headers: dict[str, str] | None,
    timeout: int,
    impersonate: str,
    proxy: str | None,
) -> StealthResponse:
    """Fetch via curl_cffi with browser-grade TLS fingerprint."""
    from curl_cffi import requests as cc_requests

    final_headers = _headers_for(impersonate)
    if headers:
        final_headers.update(headers)

    # curl_cffi.requests.AsyncSession is the async API; allow_redirects
    # follows the same semantics as httpx.follow_redirects=True.
    try:
        async with cc_requests.AsyncSession(
            impersonate=impersonate, timeout=timeout,
        ) as session:
            resp = await session.get(
                url,
                headers=final_headers,
                proxy=proxy,
                allow_redirects=True,
            )
        return StealthResponse(
            text=resp.text or "",
            status_code=resp.status_code,
            headers=dict(resp.headers),
            url=str(resp.url),
            backend="curl_cffi",
        )
    except Exception as exc:
        logger.debug("curl_cffi fetch failed for %s: %s", url, exc)
        return StealthResponse(
            backend="curl_cffi",
            error=f"{type(exc).__name__}: {exc}",
            url=url,
        )


async def _fetch_httpx(
    url: str,
    *,
    headers: dict[str, str] | None,
    timeout: int,
    impersonate: str,
    proxy: str | None,
) -> StealthResponse:
    """Fallback path when curl_cffi isn't installed or fails. Plain httpx
    plus our header-spoofing dict — won't beat JA3 fingerprint blocks but
    succeeds on most non-hardened sites."""
    import httpx

    final_headers = _headers_for(impersonate)
    if headers:
        final_headers.update(headers)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            proxy=proxy,
        ) as client:
            resp = await client.get(url, headers=final_headers)
        return StealthResponse(
            text=resp.text,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            url=str(resp.url),
            backend="httpx",
        )
    except Exception as exc:
        logger.debug("httpx fallback fetch failed for %s: %s", url, exc)
        return StealthResponse(
            backend="httpx",
            error=f"{type(exc).__name__}: {exc}",
            url=url,
        )


# Public valid impersonate modes. curl_cffi accepts more but these are
# the ones we've tested header profiles for.
_VALID_IMPERSONATE = ("chrome", "chrome120", "safari17", "firefox", "edge")


async def stealth_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    impersonate: str = "chrome",
    proxy: str | None = None,
) -> StealthResponse:
    """Fetch ``url`` with browser-grade TLS fingerprint when possible.

    Tries ``curl_cffi`` with the requested ``impersonate`` mode first.
    Falls back to ``httpx`` cleanly when curl_cffi isn't installed.

    Args:
        url: Target URL.
        headers: Optional caller-provided headers — merged on top of the
            built-in browser header profile (caller wins on conflicts).
        timeout: Seconds. Default 30.
        impersonate: One of chrome / chrome120 / safari17 / firefox / edge.
            Unknown values fall back to chrome internally.
        proxy: Optional proxy URL like ``http://user:pass@host:port``.

    Returns:
        StealthResponse — never raises on transport errors. Check
        ``.ok`` / ``.status_code`` / ``.error``.
    """
    if impersonate not in _VALID_IMPERSONATE:
        logger.debug(
            "Unknown impersonate=%r — using chrome profile", impersonate,
        )
        impersonate = "chrome"

    if _is_curl_cffi_available():
        result = await _fetch_curl_cffi(
            url, headers=headers, timeout=timeout,
            impersonate=impersonate, proxy=proxy,
        )
        # On curl_cffi network failure (status 0), fall through to httpx
        # so a transient curl_cffi bug doesn't take the whole request
        # down. On a real HTTP non-200 (e.g. 403, 503), don't retry —
        # the site simply rejected us; httpx won't help.
        if result.status_code != 0:
            return result
        logger.debug(
            "curl_cffi returned no status for %s, retrying via httpx", url,
        )

    return await _fetch_httpx(
        url, headers=headers, timeout=timeout,
        impersonate=impersonate, proxy=proxy,
    )


async def stealth_get_with_rotation(
    url: str,
    *,
    impersonate_pool: list[str] | None = None,
    **kwargs: Any,
) -> StealthResponse:
    """Convenience helper for batch jobs — picks a random impersonate
    profile from ``impersonate_pool`` so consecutive requests don't all
    look like the same Chrome version. Pairs naturally with proxy
    rotation when both are wired.
    """
    pool = impersonate_pool or ["chrome", "safari17", "firefox", "edge"]
    return await stealth_get(url, impersonate=random.choice(pool), **kwargs)
