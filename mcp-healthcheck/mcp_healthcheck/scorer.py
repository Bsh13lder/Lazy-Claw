"""Composite scoring and leaderboard generation."""
from __future__ import annotations

from dataclasses import dataclass

from mcp_healthcheck.history import ProviderSummary


@dataclass(frozen=True)
class ScoredProvider:
    """A provider with its composite and component scores."""

    name: str
    score: float
    speed_score: float
    uptime_score: float
    quality_score: float
    summary: ProviderSummary


# Upper bound: latency above this gets speed_score = 0.0
_MAX_LATENCY_MS = 5000.0


def score_provider(
    summary: ProviderSummary,
    speed_weight: float = 0.4,
    uptime_weight: float = 0.3,
    quality_weight: float = 0.3,
) -> ScoredProvider:
    """Score a single provider from its summary stats."""
    if summary.total_checks == 0:
        return ScoredProvider(
            name=summary.name,
            score=0.0,
            speed_score=0.0,
            uptime_score=0.0,
            quality_score=0.0,
            summary=summary,
        )

    # Speed: normalized inverse of avg latency (fastest=1.0, >=5000ms=0.0)
    speed = max(0.0, 1.0 - summary.avg_latency_ms / _MAX_LATENCY_MS)

    # Uptime: success_rate as 0-1
    uptime = summary.success_rate

    # Quality: success_rate for now (extensible later)
    quality = summary.success_rate

    composite = (speed * speed_weight) + (uptime * uptime_weight) + (quality * quality_weight)

    return ScoredProvider(
        name=summary.name,
        score=round(composite, 4),
        speed_score=round(speed, 4),
        uptime_score=round(uptime, 4),
        quality_score=round(quality, 4),
        summary=summary,
    )


def build_leaderboard(
    summaries: list[ProviderSummary],
    speed_weight: float = 0.4,
    uptime_weight: float = 0.3,
    quality_weight: float = 0.3,
) -> list[ScoredProvider]:
    """Score all providers and return sorted descending by composite score."""
    scored = [
        score_provider(s, speed_weight, uptime_weight, quality_weight)
        for s in summaries
    ]
    return sorted(scored, key=lambda sp: sp.score, reverse=True)
