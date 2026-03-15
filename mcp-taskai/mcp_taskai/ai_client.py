from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from mcp_taskai.config import TaskAIConfig

logger = logging.getLogger(__name__)


class AllProvidersFailedError(Exception):
    """Raised when every configured provider fails."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str | None
    default_model: str


KNOWN_PROVIDERS: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai", "llama-3.3-70b-versatile"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.0-flash"),
    "openrouter": ("https://openrouter.ai/api", "meta-llama/llama-3.3-70b-instruct:free"),
    "together": ("https://api.together.xyz", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "mistral": ("https://api.mistral.ai", "mistral-small-latest"),
    "huggingface": ("https://api-inference.huggingface.co", "meta-llama/Llama-3.3-70B-Instruct"),
    "ollama": ("http://localhost:11434", "llama3.2"),
}

_CONFIG_KEY_MAP: dict[str, str] = {
    "groq": "groq_api_key",
    "gemini": "gemini_api_key",
    "openrouter": "openrouter_api_key",
    "together": "together_api_key",
    "mistral": "mistral_api_key",
    "huggingface": "hf_api_key",
}


class AIClient:
    def __init__(self, config: TaskAIConfig) -> None:
        self._config = config
        self._providers = self._build_providers(config)

    def _build_providers(self, config: TaskAIConfig) -> list[ProviderConfig]:
        providers: list[ProviderConfig] = []
        for name, (base_url, default_model) in KNOWN_PROVIDERS.items():
            if name == "ollama":
                providers.append(ProviderConfig(
                    name="ollama",
                    base_url=config.ollama_url,
                    api_key=None,
                    default_model=default_model,
                ))
                continue
            key_attr = _CONFIG_KEY_MAP.get(name)
            api_key = getattr(config, key_attr) if key_attr else None
            if api_key:
                providers.append(ProviderConfig(
                    name=name,
                    base_url=base_url,
                    api_key=api_key,
                    default_model=default_model,
                ))

        if config.preferred_provider:
            preferred = [p for p in providers if p.name == config.preferred_provider]
            rest = [p for p in providers if p.name != config.preferred_provider]
            providers = preferred + rest

        return providers

    async def chat(self, messages: list[dict], max_tokens: int = 1024) -> str:
        errors: list[str] = []
        for provider in self._providers:
            try:
                return await self._call_provider(provider, messages, max_tokens)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning("Rate limited by %s, trying next", provider.name)
                else:
                    logger.warning("Provider %s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
                continue
            except Exception as exc:
                logger.warning("Provider %s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
                continue

        raise AllProvidersFailedError(f"All providers failed: {'; '.join(errors)}")

    async def _call_provider(
        self, provider: ProviderConfig, messages: list[dict], max_tokens: int
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"

        body = {
            "model": provider.default_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        url = self._build_url(provider)

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()

        data = response.json()
        return data["choices"][0]["message"]["content"]

    def _build_url(self, provider: ProviderConfig) -> str:
        base = provider.base_url.rstrip("/")
        if provider.name == "huggingface":
            return f"{base}/models/{provider.default_model}/v1/chat/completions"
        return f"{base}/v1/chat/completions"

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]
