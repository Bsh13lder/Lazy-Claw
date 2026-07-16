"""Per-provider sliding window rate limit tracker.

Tracks request counts per time window to enable pre-emptive switching
before hitting HTTP 429. Each provider has independent counters.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderLimits:
    """Known rate limits for a provider."""

    requests_per_minute: int = 0  # 0 = unlimited
    requests_per_day: int = 0
    tokens_per_minute: int = 0
    requests_per_5h: int = 0      # Rolling 5h window (MiniMax Token Plan)


# MiniMax Token Plan tiers — flat monthly fee, request-count metered over
# a rolling 5-hour window. Source:
# https://platform.minimax.io/docs/guides/pricing-token-plan
MINIMAX_TIER_LIMITS: dict[str, int] = {
    "starter":         1_500,    # $10/mo
    "plus":            4_500,    # $20/mo   (default — user is on this tier)
    "plus-highspeed":  4_500,    # $40/mo
    "max":             15_000,   # $50/mo
    "max-highspeed":   15_000,   # $80/mo
    "ultra":           30_000,   # $150/mo  (alias for ultra-highspeed)
    "ultra-highspeed": 30_000,   # $150/mo  (MiniMax's brand name)
}


# Known free / subscription tier limits (conservative estimates)
KNOWN_LIMITS: dict[str, ProviderLimits] = {
    "groq": ProviderLimits(requests_per_minute=30, tokens_per_minute=14_000),
    "gemini": ProviderLimits(requests_per_minute=15, requests_per_day=500),
    "openrouter": ProviderLimits(requests_per_minute=20),
    "together": ProviderLimits(requests_per_minute=60),
    "mistral": ProviderLimits(requests_per_minute=30),
    "huggingface": ProviderLimits(requests_per_minute=10),
    "ollama": ProviderLimits(),  # Local, unlimited
    # MiniMax: Plus tier default. Override at runtime via
    # `RateLimiter.set_minimax_tier(...)` once the user picks a tier.
    "minimax": ProviderLimits(requests_per_5h=MINIMAX_TIER_LIMITS["plus"]),
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
        self._fivehour_counters: dict[str, _WindowCounter] = {}
        self._init_counters()

    def _init_counters(self) -> None:
        for name, limits in self._limits.items():
            self._minute_counters[name] = _WindowCounter(
                window_seconds=60, max_count=limits.requests_per_minute
            )
            self._day_counters[name] = _WindowCounter(
                window_seconds=86400, max_count=limits.requests_per_day
            )
            self._fivehour_counters[name] = _WindowCounter(
                window_seconds=18000, max_count=limits.requests_per_5h
            )

    def set_provider_limit(self, provider: str, limits: ProviderLimits) -> None:
        """Replace a provider's limits (used to apply MiniMax tier choice)."""
        self._limits[provider] = limits
        self._minute_counters[provider] = _WindowCounter(
            window_seconds=60, max_count=limits.requests_per_minute
        )
        self._day_counters[provider] = _WindowCounter(
            window_seconds=86400, max_count=limits.requests_per_day
        )
        self._fivehour_counters[provider] = _WindowCounter(
            window_seconds=18000, max_count=limits.requests_per_5h
        )

    def set_minimax_tier(self, tier: str) -> None:
        """Configure MiniMax 5-hour cap from Token Plan tier name."""
        cap = MINIMAX_TIER_LIMITS.get(tier.lower().strip(), MINIMAX_TIER_LIMITS["plus"])
        logger.debug(
            "[llm] rate limiter: minimax tier=%r → 5h cap=%d req", tier, cap,
        )
        self.set_provider_limit("minimax", ProviderLimits(requests_per_5h=cap))

    def has_capacity(self, provider: str) -> bool:
        """Check if provider has rate limit capacity right now."""
        now = time.monotonic()
        minute = self._minute_counters.get(provider)
        day = self._day_counters.get(provider)
        fivehour = self._fivehour_counters.get(provider)
        if minute and not minute.has_capacity(now):
            return False
        if day and not day.has_capacity(now):
            return False
        if fivehour and not fivehour.has_capacity(now):
            return False
        return True

    def record_request(self, provider: str) -> None:
        """Record that a request was made to this provider."""
        now = time.monotonic()
        minute = self._minute_counters.get(provider)
        day = self._day_counters.get(provider)
        fivehour = self._fivehour_counters.get(provider)
        if minute:
            minute.record(now)
        if day:
            day.record(now)
        if fivehour:
            fivehour.record(now)

    def wait_seconds(self, provider: str) -> float:
        """Seconds until this provider has capacity again."""
        now = time.monotonic()
        minute_wait = 0.0
        day_wait = 0.0
        fivehour_wait = 0.0
        minute = self._minute_counters.get(provider)
        day = self._day_counters.get(provider)
        fivehour = self._fivehour_counters.get(provider)
        if minute:
            minute_wait = minute.wait_seconds(now)
        if day:
            day_wait = day.wait_seconds(now)
        if fivehour:
            fivehour_wait = fivehour.wait_seconds(now)
        return max(minute_wait, day_wait, fivehour_wait)

    def get_available_providers(self, providers: list[str]) -> list[str]:
        """Filter to providers that currently have capacity."""
        return [p for p in providers if self.has_capacity(p)]

    def record_rate_limit_hit(self, provider: str) -> None:
        """Called when we get a 429 — fill the active window to block retries.

        For providers with a 5h window (MiniMax) we saturate that window
        so the limiter blocks until the rolling cap actually opens up.
        For minute-windowed providers we fill the minute counter as before.
        """
        now = time.monotonic()
        fivehour = self._fivehour_counters.get(provider)
        if fivehour and fivehour.max_count > 0:
            fivehour._prune(now)
            remaining = fivehour.max_count - len(fivehour.timestamps)
            logger.warning(
                "[llm] rate-limit hit for provider=%s — saturating 5h window "
                "(%d slots) to block retries until it opens",
                provider, remaining,
            )
            for _ in range(remaining):
                fivehour.record(now)
            return
        minute = self._minute_counters.get(provider)
        if minute and minute.max_count > 0:
            minute._prune(now)
            remaining = minute.max_count - len(minute.timestamps)
            logger.warning(
                "[llm] rate-limit hit for provider=%s — saturating 1m window "
                "(%d slots) to block retries",
                provider, remaining,
            )
            for _ in range(remaining):
                minute.record(now)

    def get_status(self) -> dict[str, dict]:
        """Return current rate limit status for all providers."""
        now = time.monotonic()
        result = {}
        for name in self._limits:
            minute = self._minute_counters.get(name)
            day = self._day_counters.get(name)
            fivehour = self._fivehour_counters.get(name)
            minute_used = 0
            minute_max = 0
            day_used = 0
            day_max = 0
            fivehour_used = 0
            fivehour_max = 0
            fivehour_reset_seconds = 0.0
            if minute:
                minute._prune(now)
                minute_used = len(minute.timestamps)
                minute_max = minute.max_count
            if day:
                day._prune(now)
                day_used = len(day.timestamps)
                day_max = day.max_count
            if fivehour:
                fivehour._prune(now)
                fivehour_used = len(fivehour.timestamps)
                fivehour_max = fivehour.max_count
                if fivehour_max > 0 and fivehour.timestamps:
                    oldest = fivehour.timestamps[0]
                    fivehour_reset_seconds = max(
                        0.0, (oldest + fivehour.window_seconds) - now
                    )
            result[name] = {
                "has_capacity": self.has_capacity(name),
                "minute_used": minute_used,
                "minute_max": minute_max,
                "day_used": day_used,
                "day_max": day_max,
                "fivehour_used": fivehour_used,
                "fivehour_max": fivehour_max,
                "fivehour_reset_seconds": round(fivehour_reset_seconds, 1),
                "wait_seconds": round(self.wait_seconds(name), 1),
            }
        return result
