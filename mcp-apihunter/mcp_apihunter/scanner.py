"""Auto-discovery scanner for free AI API endpoints."""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from mcp_apihunter.models import ScanResult, ScanReport

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Known free-tier providers that can be probed without API keys
KNOWN_FREE_TIERS = [
    {
        "name": "cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_env": "CEREBRAS_API_KEY",
        "models": ("llama-4-scout-17b-16e-instruct", "llama3.1-8b"),
    },
]


async def scan_openrouter_free() -> list[ScanResult]:
    """Query OpenRouter API for all free models."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(OPENROUTER_MODELS_URL)
        if resp.status_code != 200:
            logger.warning("OpenRouter models API returned %d", resp.status_code)
            return []
        data = resp.json()
        models_list = data.get("data", [])
        free_models = [
            m["id"] for m in models_list
            if isinstance(m.get("id"), str) and m["id"].endswith(":free")
        ]
        if not free_models:
            return []
        return [ScanResult(
            name="openrouter-free",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
            models=tuple(free_models),
            source="openrouter-scan",
        )]
    except Exception as exc:
        logger.warning("OpenRouter scan failed: %s", exc)
        return []


async def scan_ollama_local(ollama_url: str = "http://localhost:11434") -> list[ScanResult]:
    """Detect local Ollama and list installed models."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ollama_url.rstrip('/')}/api/tags")
        if resp.status_code != 200:
            return []
        data = resp.json()
        models_raw = data.get("models", [])
        model_names = [m["name"] for m in models_raw if isinstance(m.get("name"), str)]
        if not model_names:
            return []
        return [ScanResult(
            name="ollama",
            base_url=ollama_url,
            api_key_env=None,
            models=tuple(model_names),
            source="ollama-local",
        )]
    except Exception as exc:
        logger.debug("Ollama scan failed (likely not running): %s", exc)
        return []


async def scan_known_free_tiers() -> list[ScanResult]:
    """Probe known free-tier API base URLs for reachability."""
    results: list[ScanResult] = []
    for provider in KNOWN_FREE_TIERS:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(provider["base_url"].rstrip("/") + "/models")
            if resp.status_code in (200, 401, 403):
                # 401/403 means endpoint exists but needs auth — still valid
                results.append(ScanResult(
                    name=provider["name"],
                    base_url=provider["base_url"],
                    api_key_env=provider["api_key_env"],
                    models=tuple(provider["models"]),
                    source="known-free-tier",
                ))
        except Exception as exc:
            logger.debug("Free tier probe failed for %s: %s", provider["name"], exc)
    return results


async def run_full_scan(registry, config) -> ScanReport:
    """Run all scanners and update the registry with discovered endpoints."""
    ollama_url = getattr(config, "ollama_url", "http://localhost:11434")

    # Run all scanners concurrently
    scanner_results = await asyncio.gather(
        scan_openrouter_free(),
        scan_ollama_local(ollama_url),
        scan_known_free_tiers(),
        return_exceptions=True,
    )

    discovered = 0
    added = 0
    updated = 0
    errors: list[str] = []

    for result in scanner_results:
        if isinstance(result, Exception):
            errors.append(str(result))
            continue
        if not isinstance(result, list):
            continue
        for scan_result in result:
            discovered += 1
            try:
                existing = await registry.find_by_name(scan_result.name)
                if existing is None:
                    await registry.add(
                        name=scan_result.name,
                        base_url=scan_result.base_url,
                        api_key_env=scan_result.api_key_env,
                        models=list(scan_result.models),
                        added_by=f"auto-scanner:{scan_result.source}",
                    )
                    added += 1
                    logger.info(
                        "Scanner added new provider: %s (%d models)",
                        scan_result.name, len(scan_result.models),
                    )
                else:
                    # Update model list if changed
                    if set(scan_result.models) != set(existing.models):
                        await registry.update_models(existing.id, list(scan_result.models))
                        updated += 1
                        logger.info(
                            "Scanner updated models for %s: %d -> %d",
                            scan_result.name, len(existing.models), len(scan_result.models),
                        )
            except Exception as exc:
                errors.append(f"{scan_result.name}: {exc}")

    return ScanReport(
        discovered=discovered,
        added=added,
        updated=updated,
        errors=tuple(errors),
        timestamp=time.time(),
    )
