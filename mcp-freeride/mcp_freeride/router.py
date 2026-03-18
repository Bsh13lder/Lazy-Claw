from __future__ import annotations

import logging
import os
import time

from mcp_freeride.config import FreeRideConfig
from mcp_freeride.health import HealthChecker
from mcp_freeride.providers.base import BaseProvider, OpenAICompatibleProvider, RateLimitError

logger = logging.getLogger(__name__)


class AllProvidersFailedError(Exception):
    """Raised when every configured provider fails."""


class _DynamicProvider(OpenAICompatibleProvider):
    """Provider created dynamically from an apihunter registry entry."""

    def __init__(self, name: str, base_url: str, api_key: str | None, models: list[str]) -> None:
        super().__init__(
            provider_name=name,
            base_url=base_url,
            api_key=api_key,
            default_model=models[0] if models else "default",
            available_models=models,
        )


class FreeRideRouter:
    def __init__(self, config: FreeRideConfig) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._health = HealthChecker()

        from mcp_freeride.providers.groq import GroqProvider
        from mcp_freeride.providers.gemini import GeminiProvider
        from mcp_freeride.providers.openrouter import OpenRouterProvider
        from mcp_freeride.providers.together import TogetherProvider
        from mcp_freeride.providers.mistral import MistralProvider
        from mcp_freeride.providers.huggingface import HuggingFaceProvider
        from mcp_freeride.providers.ollama import OllamaProvider

        def _ollama_reachable(url: str) -> bool:
            """Quick TCP probe — no HTTP, no httpx logging."""
            import socket
            from urllib.parse import urlparse
            try:
                p = urlparse(url)
                s = socket.create_connection(
                    (p.hostname or "localhost", p.port or 11434), timeout=0.5,
                )
                s.close()
                return True
            except (OSError, ValueError):
                return False

        if config.groq_api_key:
            self._providers["groq"] = GroqProvider(config.groq_api_key)
        if config.gemini_api_key:
            self._providers["gemini"] = GeminiProvider(config.gemini_api_key)
        if config.openrouter_api_key:
            self._providers["openrouter"] = OpenRouterProvider(config.openrouter_api_key)
        if config.together_api_key:
            self._providers["together"] = TogetherProvider(config.together_api_key)
        if config.mistral_api_key:
            self._providers["mistral"] = MistralProvider(config.mistral_api_key)
        if config.hf_api_key:
            self._providers["huggingface"] = HuggingFaceProvider(config.hf_api_key)
        # Ollama only if actually reachable (avoids httpx spam when not running)
        if _ollama_reachable(config.ollama_url):
            self._providers["ollama"] = OllamaProvider(config.ollama_url)
            logger.info("Ollama detected at %s", config.ollama_url)

    def _load_apihunter_providers(self) -> None:
        """Load validated endpoints from mcp-apihunter as dynamic providers."""
        try:
            import asyncio
            from mcp_apihunter.config import ApiHunterConfig
            from mcp_apihunter.registry import Registry

            config = ApiHunterConfig()
            registry = Registry(config.db_path)

            async def _load():
                await registry.init_db()
                return await registry.list_all(status_filter="active")

            try:
                loop = asyncio.get_running_loop()
                # Already in async context — schedule for later
                return
            except RuntimeError:
                pass

            entries = asyncio.run(_load())
            for entry in entries:
                name = f"apihunter_{entry.name}"
                if name in self._providers:
                    continue
                api_key = None
                if entry.api_key_env:
                    api_key = os.getenv(entry.api_key_env)
                self._providers[name] = _DynamicProvider(
                    name=name,
                    base_url=entry.base_url,
                    api_key=api_key,
                    models=list(entry.models),
                )
                logger.info("Loaded apihunter endpoint: %s (%s)", entry.name, entry.base_url)

        except ImportError:
            logger.debug("mcp-apihunter not installed, skipping dynamic providers")
        except Exception:
            logger.debug("Failed to load apihunter providers", exc_info=True)

    async def load_apihunter_providers_async(self) -> int:
        """Async version: load validated endpoints from mcp-apihunter."""
        count = 0
        try:
            from mcp_apihunter.config import ApiHunterConfig
            from mcp_apihunter.registry import Registry

            config = ApiHunterConfig()
            registry = Registry(config.db_path)
            await registry.init_db()
            entries = await registry.list_all(status_filter="active")

            for entry in entries:
                name = f"apihunter_{entry.name}"
                if name in self._providers:
                    continue
                api_key = None
                if entry.api_key_env:
                    api_key = os.getenv(entry.api_key_env)
                self._providers[name] = _DynamicProvider(
                    name=name,
                    base_url=entry.base_url,
                    api_key=api_key,
                    models=list(entry.models),
                )
                logger.info("Loaded apihunter endpoint: %s (%s)", entry.name, entry.base_url)
                count += 1
        except ImportError:
            pass
        except Exception:
            logger.debug("Failed to load apihunter providers", exc_info=True)
        return count

    async def chat(self, messages: list[dict], model: str | None = None) -> dict:
        provider_hint: str | None = None
        if model and "/" in model:
            provider_hint, model = model.split("/", 1)

        provider_names = list(self._providers.keys())
        ranked = self._health.get_ranked_providers(provider_names)

        # If there's a provider hint, try it first
        if provider_hint and provider_hint in self._providers:
            ranked = [provider_hint] + [n for n in ranked if n != provider_hint]

        for name in ranked:
            provider = self._providers[name]
            start = time.monotonic()
            try:
                result = await provider.chat(messages, model)
                latency_ms = (time.monotonic() - start) * 1000
                self._health.record_success(name, latency_ms)
                return result
            except RateLimitError:
                self._health.record_failure(name)
                logger.warning("Rate limited by %s, trying next provider", name)
                continue
            except Exception as exc:
                self._health.record_failure(name)
                logger.warning("Provider %s failed: %s, trying next", name, exc)
                continue

        raise AllProvidersFailedError("All configured providers failed")

    def list_models(self) -> list[dict]:
        """Return list of all available models across all providers."""
        result = []
        for name, provider in self._providers.items():
            stats = self._health._get_stats(name)
            for model_id in provider.models:
                result.append({
                    "provider": name,
                    "model": model_id,
                    "healthy": stats.is_healthy,
                    "avg_latency_ms": round(stats.avg_latency_ms, 1),
                })
        return result

    async def refresh_ollama(self) -> list[str]:
        """Refresh Ollama's model list from /api/tags."""
        provider = self._providers.get("ollama")
        if provider is None:
            return []
        return await provider.refresh_models()

    def get_status(self) -> dict:
        """Return health checker status."""
        return self._health.get_status()
