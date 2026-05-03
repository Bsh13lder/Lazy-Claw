"""Proxy rotator with cyclic / random / sticky strategies and health tracking.

Why this exists. Today every mcp-scraper request egresses from one IP.
When that IP hits a 429 on rapid scrapes (job-search, Reddit, batch
business research), the rate-limit propagates and every subsequent
request gets blocked. With a small proxy pool and rotation, batch jobs
survive transient rate-limits and recover automatically.

The rotator is **transport-agnostic**. It hands out proxy URL strings;
``stealth_http.stealth_get(url, proxy=...)`` and crawl4ai's
``BrowserConfig(proxy_config=...)`` both accept this shape.

Rotation strategies:
  * cyclic — round-robin (default; predictable load distribution)
  * random — uniform random pick from healthy pool
  * sticky — same proxy reused for all requests to one host within a
    session, rotates only when sticky proxy goes unhealthy. Best for
    sites that issue session cookies tied to client IP.

Health tracking. After 3 consecutive ``mark_bad()`` calls a proxy goes
into a 60-second cooldown. The rotator skips it during cooldown and
auto-restores it after. ``mark_good()`` resets the strike count.

Approach inspired by Scrapling's ``ProxyRotator`` (BSD-3) but
implemented independently — round-robin with health tracking is a
standard pattern, not Scrapling-specific code.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

logger = logging.getLogger(__name__)

Strategy = Literal["cyclic", "random", "sticky"]

# Strike threshold + cooldown — tuned for typical 429 (rate limit)
# behaviour: a proxy that hits 3 strikes in a row is almost certainly
# being throttled. 60s cooldown matches Brave Search / SerpAPI /
# typical CDN rate-limit windows.
_STRIKE_THRESHOLD = 3
_COOLDOWN_SECONDS = 60.0


@dataclass
class _ProxyHealth:
    """Per-proxy state. Not exposed to callers — internal bookkeeping."""

    strikes: int = 0
    cooldown_until: float = 0.0  # monotonic seconds — 0 means "available"

    def is_available(self, now: float) -> bool:
        return now >= self.cooldown_until

    def reset(self) -> None:
        self.strikes = 0
        self.cooldown_until = 0.0


@dataclass
class ProxyRotator:
    """Thread-safe proxy rotator with strategy + health tracking.

    Attributes:
        proxies: List of proxy URL strings. Order is preserved for cyclic.
        strategy: cyclic / random / sticky.
    """

    proxies: list[str]
    strategy: Strategy = "cyclic"
    _idx: int = field(default=0, init=False)
    _sticky_for_host: dict[str, str] = field(default_factory=dict, init=False)
    _health: dict[str, _ProxyHealth] = field(default_factory=dict, init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.proxies:
            raise ValueError("ProxyRotator requires a non-empty proxy list")
        if self.strategy not in ("cyclic", "random", "sticky"):
            raise ValueError(
                f"Unknown strategy {self.strategy!r}; "
                "use cyclic / random / sticky"
            )
        for p in self.proxies:
            self._health[p] = _ProxyHealth()

    # ── Selection ──────────────────────────────────────────────────────

    def next(self, *, host: str | None = None) -> str:
        """Return the next proxy. Skips proxies in cooldown.

        Args:
            host: Required for ``strategy='sticky'``; ignored otherwise.
                When sticky and ``host`` is None, falls back to cyclic.

        Raises:
            RuntimeError: When every proxy is in cooldown.
        """
        with self._lock:
            now = time.monotonic()
            healthy = [p for p in self.proxies if self._health[p].is_available(now)]
            if not healthy:
                # Surface so callers can pause briefly instead of slamming
                # the same dead pool. Empirically a 60s cooldown matches
                # most CDN unblock windows so this is rare in practice.
                raise RuntimeError(
                    f"All {len(self.proxies)} proxies in cooldown. "
                    f"Wait ~{int(_COOLDOWN_SECONDS)}s and retry."
                )

            if self.strategy == "random":
                return random.choice(healthy)

            if self.strategy == "sticky" and host:
                # Reuse the host's pinned proxy as long as it's healthy
                pinned = self._sticky_for_host.get(host)
                if pinned and pinned in healthy:
                    return pinned
                # Pinned proxy went unhealthy (or first request for host)
                # — pick a new one cyclically and remember it.
                pick = self._next_cyclic(healthy)
                self._sticky_for_host[host] = pick
                return pick

            # Default cyclic — also covers sticky-without-host
            return self._next_cyclic(healthy)

    def _next_cyclic(self, healthy: list[str]) -> str:
        """Round-robin within the healthy subset. Holds the lock already."""
        # Use the global index but hop to the next healthy entry from there.
        n = len(self.proxies)
        for _ in range(n):
            candidate = self.proxies[self._idx % n]
            self._idx = (self._idx + 1) % n
            if candidate in self._health and self._health[candidate].is_available(time.monotonic()):
                return candidate
        # Fallback — should not hit since `healthy` was non-empty
        return healthy[0]

    # ── Health signals ─────────────────────────────────────────────────

    def mark_bad(self, proxy: str, *, reason: str = "") -> None:
        """Record a failure on this proxy. After ``_STRIKE_THRESHOLD``
        consecutive failures the proxy enters cooldown for
        ``_COOLDOWN_SECONDS`` seconds."""
        with self._lock:
            health = self._health.get(proxy)
            if health is None:
                logger.debug("mark_bad on unknown proxy %r — ignoring", proxy)
                return
            health.strikes += 1
            if health.strikes >= _STRIKE_THRESHOLD:
                health.cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
                logger.info(
                    "Proxy %s blacklisted for %ds (reason=%s)",
                    proxy, int(_COOLDOWN_SECONDS), reason or "<n/a>",
                )

    def mark_good(self, proxy: str) -> None:
        """Reset strike count + cooldown for a proxy that just succeeded."""
        with self._lock:
            health = self._health.get(proxy)
            if health is not None:
                health.reset()

    # ── Introspection ──────────────────────────────────────────────────

    def stats(self) -> dict:
        """Snapshot for debugging / Web UI."""
        with self._lock:
            now = time.monotonic()
            return {
                "strategy": self.strategy,
                "total": len(self.proxies),
                "healthy": sum(
                    1 for h in self._health.values() if h.is_available(now)
                ),
                "in_cooldown": [
                    {"proxy": p, "cooldown_remaining_s": max(0, h.cooldown_until - now)}
                    for p, h in self._health.items() if not h.is_available(now)
                ],
                "sticky_assignments": dict(self._sticky_for_host),
            }
