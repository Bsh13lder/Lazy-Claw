"""Background ping loop and facade for health-check data."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from mcp_healthcheck.config import HealthCheckConfig
from mcp_healthcheck.history import CheckHistory, ProviderSummary
from mcp_healthcheck.providers import (
    ProviderEndpoint,
    PingResult,
    build_endpoints,
    ping_provider,
)
from mcp_healthcheck.scorer import ScoredProvider, build_leaderboard

logger = logging.getLogger(__name__)


class Monitor:
    """Core monitor: owns endpoints, history, and the background ping loop."""

    def __init__(self, config: HealthCheckConfig) -> None:
        self._config = config
        self._endpoints = build_endpoints(config)
        self._endpoint_map: dict[str, ProviderEndpoint] = {
            ep.name: ep for ep in self._endpoints
        }
        self._history = CheckHistory(max_size=config.history_size)
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_background_loop(self) -> None:
        """Spawn the periodic ping task. Safe to call once."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info(
                "background loop started (interval=%ds, endpoints=%d)",
                self._config.ping_interval_seconds,
                len(self._endpoints),
            )

    async def _loop(self) -> None:
        """Run _ping_all every interval until cancelled."""
        while True:
            await self._ping_all()
            await asyncio.sleep(self._config.ping_interval_seconds)

    async def _ping_all(self) -> None:
        """Ping every endpoint concurrently and record results."""
        if not self._endpoints:
            return
        results: list[PingResult] = await asyncio.gather(
            *(ping_provider(ep) for ep in self._endpoints)
        )
        for ep, result in zip(self._endpoints, results):
            self._history.record(ep.name, result)
            status = "OK" if result.success else f"FAIL ({result.error})"
            logger.debug("ping %s: %s %.0fms", ep.name, status, result.latency_ms)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ping_one(self, provider_name: str) -> PingResult | None:
        """Force-ping a single provider by name. Returns None if unknown."""
        ep = self._endpoint_map.get(provider_name)
        if ep is None:
            return None
        result = await ping_provider(ep)
        self._history.record(provider_name, result)
        return result

    def get_status(self) -> dict:
        """Full status dict for all monitored providers."""
        providers: list[dict] = []
        for ep in self._endpoints:
            summary = self._history.get_summary(ep.name)
            providers.append(asdict(summary))
        return {
            "monitored_providers": len(self._endpoints),
            "ping_interval_seconds": self._config.ping_interval_seconds,
            "providers": providers,
        }

    def get_leaderboard(self) -> list[ScoredProvider]:
        """Scored and ranked provider list."""
        summaries: list[ProviderSummary] = [
            self._history.get_summary(ep.name) for ep in self._endpoints
        ]
        return build_leaderboard(
            summaries,
            self._config.speed_weight,
            self._config.uptime_weight,
            self._config.quality_weight,
        )

    def get_provider_history(self, name: str, limit: int = 20) -> list[PingResult]:
        """Recent ping results for a single provider."""
        return self._history.get_history(name, limit)

    @property
    def endpoint_names(self) -> list[str]:
        """Names of all monitored endpoints."""
        return [ep.name for ep in self._endpoints]
