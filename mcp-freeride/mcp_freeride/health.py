from __future__ import annotations
import time
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ProviderStats:
    total_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    recent_latencies: deque = field(default_factory=lambda: deque(maxlen=50))
    last_success: float | None = None
    last_failure: float | None = None

    @property
    def avg_latency_ms(self) -> float:
        if not self.recent_latencies:
            return 0.0
        return sum(self.recent_latencies) / len(self.recent_latencies)

    @property
    def is_healthy(self) -> bool:
        # Unhealthy if 3+ consecutive failures
        return self.consecutive_failures < 3


class HealthChecker:
    def __init__(self) -> None:
        self._stats: dict[str, ProviderStats] = {}

    def _get_stats(self, provider: str) -> ProviderStats:
        if provider not in self._stats:
            self._stats[provider] = ProviderStats()
        return self._stats[provider]

    def record_success(self, provider: str, latency_ms: float) -> None:
        stats = self._get_stats(provider)
        stats.total_requests += 1
        stats.total_successes += 1
        stats.consecutive_failures = 0
        stats.recent_latencies.append(latency_ms)
        stats.last_success = time.time()

    def record_failure(self, provider: str) -> None:
        stats = self._get_stats(provider)
        stats.total_requests += 1
        stats.total_failures += 1
        stats.consecutive_failures += 1
        stats.last_failure = time.time()

    def get_ranked_providers(self, configured: list[str]) -> list[str]:
        """Return providers sorted by: healthy first, then lowest latency."""
        def sort_key(name: str) -> tuple:
            stats = self._get_stats(name)
            healthy = 0 if stats.is_healthy else 1
            latency = stats.avg_latency_ms if stats.avg_latency_ms > 0 else 9999.0
            return (healthy, latency)
        return sorted(configured, key=sort_key)

    def get_status(self) -> dict[str, dict]:
        """Return status dict for all tracked providers."""
        result = {}
        for name, stats in self._stats.items():
            result[name] = {
                "healthy": stats.is_healthy,
                "total_requests": stats.total_requests,
                "successes": stats.total_successes,
                "failures": stats.total_failures,
                "consecutive_failures": stats.consecutive_failures,
                "avg_latency_ms": round(stats.avg_latency_ms, 1),
            }
        return result
