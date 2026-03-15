"""Provider endpoints, ping logic, and known provider registry."""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from mcp_healthcheck.config import HealthCheckConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data objects (frozen / immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderEndpoint:
    """A single AI provider endpoint to monitor."""

    name: str
    base_url: str
    api_key: str | None
    default_model: str


@dataclass(frozen=True)
class PingResult:
    """Outcome of a single health-check ping."""

    success: bool
    latency_ms: float
    error: str | None
    timestamp: str  # ISO-8601 UTC


# ---------------------------------------------------------------------------
# Known providers
# ---------------------------------------------------------------------------

KNOWN_PROVIDERS: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai", "llama-3.3-70b-versatile"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.0-flash",
    ),
    "openrouter": (
        "https://openrouter.ai/api",
        "meta-llama/llama-3.3-70b-instruct:free",
    ),
    "together": (
        "https://api.together.xyz",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    ),
    "mistral": ("https://api.mistral.ai", "mistral-small-latest"),
    "huggingface": (
        "https://api-inference.huggingface.co",
        "meta-llama/Llama-3.3-70B-Instruct",
    ),
    "ollama": ("http://localhost:11434", "llama3.2"),
}

_KEY_MAP: dict[str, str] = {
    "groq": "groq_api_key",
    "gemini": "gemini_api_key",
    "openrouter": "openrouter_api_key",
    "together": "together_api_key",
    "mistral": "mistral_api_key",
    "huggingface": "hf_api_key",
}


def build_endpoints(config: HealthCheckConfig) -> list[ProviderEndpoint]:
    """Return endpoints for every provider that has a configured key (or Ollama URL)."""
    endpoints: list[ProviderEndpoint] = []
    for name, (base_url, default_model) in KNOWN_PROVIDERS.items():
        if name == "ollama":
            url = config.ollama_url or base_url
            endpoints.append(ProviderEndpoint(name, url, None, default_model))
            continue
        key = getattr(config, _KEY_MAP[name])
        if key:
            endpoints.append(ProviderEndpoint(name, base_url, key, default_model))
    return endpoints


async def ping_provider(endpoint: ProviderEndpoint) -> PingResult:
    """Ping a single provider and return the result. Never raises."""
    now = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()

    try:
        if endpoint.name == "ollama":
            return await _ping_ollama(endpoint, start, now)
        if endpoint.name == "huggingface":
            return await _ping_huggingface(endpoint, start, now)
        return await _ping_openai_compat(endpoint, start, now)
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.monotonic() - start) * 1000
        logger.debug("ping %s failed: %s", endpoint.name, exc)
        return PingResult(success=False, latency_ms=elapsed, error=str(exc), timestamp=now)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _ping_openai_compat(
    ep: ProviderEndpoint, start: float, now: str
) -> PingResult:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if ep.api_key:
        headers["Authorization"] = f"Bearer {ep.api_key}"
    body = {
        "model": ep.default_model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{ep.base_url}/v1/chat/completions", headers=headers, json=body)
    elapsed = (time.monotonic() - start) * 1000
    if resp.status_code == 200:
        return PingResult(success=True, latency_ms=elapsed, error=None, timestamp=now)
    return PingResult(success=False, latency_ms=elapsed, error=f"HTTP {resp.status_code}", timestamp=now)


async def _ping_huggingface(
    ep: ProviderEndpoint, start: float, now: str
) -> PingResult:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if ep.api_key:
        headers["Authorization"] = f"Bearer {ep.api_key}"
    url = f"{ep.base_url}/models/{ep.default_model}/v1/chat/completions"
    body = {
        "model": ep.default_model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, json=body)
    elapsed = (time.monotonic() - start) * 1000
    if resp.status_code == 200:
        return PingResult(success=True, latency_ms=elapsed, error=None, timestamp=now)
    return PingResult(success=False, latency_ms=elapsed, error=f"HTTP {resp.status_code}", timestamp=now)


async def _ping_ollama(
    ep: ProviderEndpoint, start: float, now: str
) -> PingResult:
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{ep.base_url}/api/tags")
    elapsed = (time.monotonic() - start) * 1000
    if resp.status_code == 200:
        return PingResult(success=True, latency_ms=elapsed, error=None, timestamp=now)
    return PingResult(success=False, latency_ms=elapsed, error=f"HTTP {resp.status_code}", timestamp=now)
