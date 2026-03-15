"""Per-provider sliding window rate limit tracker.

Tracks request counts per time window to enable pre-emptive switching
before hitting HTTP 429. Each provider has independent counters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderLimits:
    """Known rate limits for a provider."""

    requests_per_minute: int = 0  # 0 = unlimited
    requests_per_day: int = 0
    tokens_per_minute: int = 0


# Known free tier limits (conservative estimates)
KNOWN_LIMITS: dict[str, ProviderLimits] = {
    "groq": ProviderLimits(requests_per_minute=30, tokens_per_minute=14_000),
    "gemini": ProviderLimits(requests_per_minute=15, requests_per_day=500),
    "openrouter": ProviderLimits(requests_per_minute=20),
    "together": ProviderLimits(requests_per_minute=60),
    "mistral": ProviderLimits(requests_per_minute=30),
    "huggingface": ProviderLimits(requests_per_minute=10),
    "ollama": ProviderLimits(),  # Local, unlimited
}


@dataclass
class _WindowCounter:
    """Sliding window counter for a single time period."""

    window_seconds: int
    max_count: int
    timestamps: list[float] = field(default_factory=list)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self.timestamps = [t for t in self.timestamps if t > cutoff]

    def record(self, now: float) -> None:
        self.timestamps.append(now)

    def has_capacity(self, now: float) -> bool:
        if self.max_count <= 0:
            return True  # Unlimited
        self._prune(now)
        return len(self.timestamps) < self.max_count

    def wait_seconds(self, now: float) -> float:
        """Seconds until a slot opens. Returns 0 if capacity available."""
        if self.has_capacity(now):
            return 0.0
        self._prune(now)
        if not self.timestamps:
            return 0.0
        oldest = self.timestamps[0]
        return max(0.0, (oldest + self.window_seconds) - now)


class RateLimiter:
    """Tracks rate limits across all free providers.

    Usage:
        limiter = RateLimiter()
        if limiter.has_capacity("groq"):
            # safe to call
            limiter.record_request("groq")
        else:
            wait = limiter.wait_seconds("groq")
            # either wait or try next provider
    """

    def __init__(self, custom_limits: dict[str, ProviderLimits] | None = None) -> None:
        self._limits = dict(KNOWN_LIMITS)
        if custom_limits:
            self._limits.update(custom_limits)
        self._minute_counters: dict[str, _WindowCounter] = {}
        self._day_counters: dict[str, _WindowCounter] = {}
        self._init_counters()

    def _init_counters(self) -> None:
        for name, limits in self._limits.items():
            self._minute_counters[name] = _WindowCounter(
                window_seconds=60, max_count=limits.requests_per_minute
            )
            self._day_counters[name] = _WindowCounter(
                window_seconds=86400, max_count=limits.requests_per_day
            )

    def has_capacity(self, provider: str) -> bool:
        """Check if provider has rate limit capacity right now."""
        now = time.monotonic()
        minute = self._minute_counters.get(provider)
        day = self._day_counters.get(provider)
        if minute and not minute.has_capacity(now):
            return False
        if day and not day.has_capacity(now):
            return False
        return True

    def record_request(self, provider: str) -> None:
        """Record that a request was made to this provider."""
        now = time.monotonic()
        minute = self._minute_counters.get(provider)
        day = self._day_counters.get(provider)
        if minute:
            minute.record(now)
        if day:
            day.record(now)

    def wait_seconds(self, provider: str) -> float:
        """Seconds until this provider has capacity again."""
        now = time.monotonic()
        minute_wait = 0.0
        day_wait = 0.0
        minute = self._minute_counters.get(provider)
        day = self._day_counters.get(provider)
        if minute:
            minute_wait = minute.wait_seconds(now)
        if day:
            day_wait = day.wait_seconds(now)
        return max(minute_wait, day_wait)

    def get_available_providers(self, providers: list[str]) -> list[str]:
        """Filter to providers that currently have capacity."""
        return [p for p in providers if self.has_capacity(p)]

    def record_rate_limit_hit(self, provider: str) -> None:
        """Called when we get a 429 — fill up the minute window to block it."""
        now = time.monotonic()
        minute = self._minute_counters.get(provider)
        if minute and minute.max_count > 0:
            # Fill remaining slots to prevent immediate retry
            minute._prune(now)
            remaining = minute.max_count - len(minute.timestamps)
            for _ in range(remaining):
                minute.record(now)

    def get_status(self) -> dict[str, dict]:
        """Return current rate limit status for all providers."""
        now = time.monotonic()
        result = {}
        for name in self._limits:
            minute = self._minute_counters.get(name)
            day = self._day_counters.get(name)
            minute_used = 0
            minute_max = 0
            day_used = 0
            day_max = 0
            if minute:
                minute._prune(now)
                minute_used = len(minute.timestamps)
                minute_max = minute.max_count
            if day:
                day._prune(now)
                day_used = len(day.timestamps)
                day_max = day.max_count
            result[name] = {
                "has_capacity": self.has_capacity(name),
                "minute_used": minute_used,
                "minute_max": minute_max,
                "day_used": day_used,
                "day_max": day_max,
                "wait_seconds": round(self.wait_seconds(name), 1),
            }
        return result
