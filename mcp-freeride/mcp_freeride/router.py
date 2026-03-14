from __future__ import annotations

import logging
import time

from mcp_freeride.config import FreeRideConfig
from mcp_freeride.health import HealthChecker
from mcp_freeride.providers.base import BaseProvider, RateLimitError

logger = logging.getLogger(__name__)


class AllProvidersFailedError(Exception):
    """Raised when every configured provider fails."""


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
        # Ollama always available
        self._providers["ollama"] = OllamaProvider(config.ollama_url)

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

    def get_status(self) -> dict:
        """Return health checker status."""
        return self._health.get_status()
