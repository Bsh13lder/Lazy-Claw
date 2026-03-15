"""HealthCheck configuration — loads API keys and tuning params from env."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class HealthCheckConfig:
    """Immutable config for the health-check monitor."""

    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    together_api_key: str | None = None
    mistral_api_key: str | None = None
    hf_api_key: str | None = None
    ollama_url: str | None = None

    ping_interval_seconds: int = 60
    history_size: int = 100

    speed_weight: float = 0.4
    uptime_weight: float = 0.3
    quality_weight: float = 0.3


def load_config() -> HealthCheckConfig:
    """Build config from environment variables."""
    return HealthCheckConfig(
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        together_api_key=os.getenv("TOGETHER_API_KEY") or None,
        mistral_api_key=os.getenv("MISTRAL_API_KEY") or None,
        hf_api_key=os.getenv("HF_API_KEY") or None,
        ollama_url=os.getenv("OLLAMA_URL") or None,
        ping_interval_seconds=int(os.getenv("HEALTHCHECK_INTERVAL", "60")),
        history_size=int(os.getenv("HEALTHCHECK_HISTORY_SIZE", "100")),
        speed_weight=float(os.getenv("HEALTHCHECK_SPEED_WEIGHT", "0.4")),
        uptime_weight=float(os.getenv("HEALTHCHECK_UPTIME_WEIGHT", "0.3")),
        quality_weight=float(os.getenv("HEALTHCHECK_QUALITY_WEIGHT", "0.3")),
    )
