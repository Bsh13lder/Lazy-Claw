"""Per-provider check history with summary statistics."""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass

from mcp_healthcheck.providers import PingResult


@dataclass(frozen=True)
class ProviderSummary:
    """Aggregated stats for a single provider."""

    name: str
    total_checks: int
    successes: int
    failures: int
    consecutive_failures: int
    avg_latency_ms: float
    p95_latency_ms: float
    success_rate: float
    uptime_pct: float
    last_checked: str | None


class CheckHistory:
    """Stores a bounded deque of PingResults per provider.

    Methods return new objects — the deques are only mutated via record().
    """

    def __init__(self, max_size: int = 100) -> None:
        self._max_size = max_size
        self._data: dict[str, deque[PingResult]] = {}

    def record(self, provider_name: str, result: PingResult) -> None:
        """Append a ping result for *provider_name*."""
        if provider_name not in self._data:
            self._data[provider_name] = deque(maxlen=self._max_size)
        self._data[provider_name].append(result)

    def get_history(self, provider_name: str, limit: int = 20) -> list[PingResult]:
        """Return the most recent *limit* results (newest last)."""
        buf = self._data.get(provider_name, deque())
        items = list(buf)
        return items[-limit:] if limit < len(items) else list(items)

    def get_summary(self, provider_name: str) -> ProviderSummary:
        """Compute aggregate stats from stored results."""
        buf = list(self._data.get(provider_name, deque()))

        if not buf:
            return ProviderSummary(
                name=provider_name,
                total_checks=0,
                successes=0,
                failures=0,
                consecutive_failures=0,
                avg_latency_ms=0.0,
                p95_latency_ms=0.0,
                success_rate=0.0,
                uptime_pct=0.0,
                last_checked=None,
            )

        successes = sum(1 for r in buf if r.success)
        failures = len(buf) - successes

        # Count consecutive failures from the tail
        consec = 0
        for r in reversed(buf):
            if not r.success:
                consec += 1
            else:
                break

        latencies = [r.latency_ms for r in buf if r.success]
        avg_lat = statistics.mean(latencies) if latencies else 0.0
        p95_lat = _percentile(latencies, 95) if latencies else 0.0

        rate = successes / len(buf)

        return ProviderSummary(
            name=provider_name,
            total_checks=len(buf),
            successes=successes,
            failures=failures,
            consecutive_failures=consec,
            avg_latency_ms=round(avg_lat, 1),
            p95_latency_ms=round(p95_lat, 1),
            success_rate=round(rate, 4),
            uptime_pct=round(rate * 100, 2),
            last_checked=buf[-1].timestamp,
        )

    @property
    def provider_names(self) -> list[str]:
        return list(self._data.keys())


def _percentile(data: list[float], pct: int) -> float:
    """Simple percentile without numpy."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (pct / 100) * (len(sorted_data) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])
