from __future__ import annotations

import logging
import os
import time

import httpx

from mcp_apihunter.models import RegistryEntry, ValidationResult

logger = logging.getLogger(__name__)


async def validate_endpoint(
    base_url: str,
    api_key: str | None,
    models: list[str] | tuple[str, ...],
    timeout: int,
) -> ValidationResult:
    """Test an endpoint with a minimal chat completion request."""
    if not models:
        return ValidationResult(
            success=False,
            latency_ms=0.0,
            error="No models provided to test",
            model_responded=None,
            timestamp=time.time(),
        )

    model = models[0]
    url = base_url.rstrip("/") + "/v1/chat/completions"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)

        latency_ms = (time.monotonic() - start) * 1000.0

        if response.status_code != 200:
            return ValidationResult(
                success=False,
                latency_ms=latency_ms,
                error=f"HTTP {response.status_code}: {response.text[:200]}",
                model_responded=None,
                timestamp=time.time(),
            )

        body = response.json()
        choices = body.get("choices", [])
        if not choices:
            return ValidationResult(
                success=False,
                latency_ms=latency_ms,
                error="Response has no choices",
                model_responded=None,
                timestamp=time.time(),
            )

        responded_model = body.get("model", model)
        return ValidationResult(
            success=True,
            latency_ms=latency_ms,
            error=None,
            model_responded=responded_model,
            timestamp=time.time(),
        )

    except httpx.TimeoutException:
        latency_ms = (time.monotonic() - start) * 1000.0
        return ValidationResult(
            success=False,
            latency_ms=latency_ms,
            error=f"Timeout after {timeout}s",
            model_responded=None,
            timestamp=time.time(),
        )
    except httpx.ConnectError as exc:
        return ValidationResult(
            success=False,
            latency_ms=0.0,
            error=f"Connection failed: {exc}",
            model_responded=None,
            timestamp=time.time(),
        )
    except Exception as exc:
        logger.warning("Validation error for %s: %s", base_url, exc)
        return ValidationResult(
            success=False,
            latency_ms=0.0,
            error=f"Unexpected error: {exc}",
            model_responded=None,
            timestamp=time.time(),
        )


async def validate_entry(entry: RegistryEntry, timeout: int) -> ValidationResult:
    """Validate a registry entry, resolving api_key_env if set."""
    api_key: str | None = None
    if entry.api_key_env:
        api_key = os.getenv(entry.api_key_env)

    return await validate_endpoint(entry.base_url, api_key, entry.models, timeout)
