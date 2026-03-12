from __future__ import annotations

from lazyclaw.config import Config
from lazyclaw.llm.providers.base import BaseLLMProvider, LLMMessage, LLMResponse
from lazyclaw.llm.providers.openai_provider import OpenAIProvider
from lazyclaw.llm.providers.anthropic_provider import AnthropicProvider


class LLMRouter:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._providers: dict[str, BaseLLMProvider] = {}

    def _get_provider(self, model: str) -> BaseLLMProvider:
        if model.startswith(("gpt-", "o1-", "o3-", "o4-")):
            provider_name = "openai"
        elif model.startswith("claude-"):
            provider_name = "anthropic"
        else:
            raise ValueError(f"Cannot infer provider for model: {model}")

        if provider_name in self._providers:
            return self._providers[provider_name]

        if provider_name == "openai":
            if not self._config.openai_api_key:
                raise ValueError("OpenAI API key not configured")
            provider = OpenAIProvider(self._config.openai_api_key)
        elif provider_name == "anthropic":
            if not self._config.anthropic_api_key:
                raise ValueError("Anthropic API key not configured")
            provider = AnthropicProvider(self._config.anthropic_api_key)
        else:
            raise ValueError(f"Unknown provider: {provider_name}")

        self._providers[provider_name] = provider
        return provider

    async def chat(self, messages: list[LLMMessage], model: str | None = None, **kwargs) -> LLMResponse:
        model = model or self._config.default_model
        provider = self._get_provider(model)
        return await provider.chat(messages, model, **kwargs)

    async def verify_provider(self, provider: str, api_key: str) -> bool:
        if provider == "openai":
            instance = OpenAIProvider(api_key)
        elif provider == "anthropic":
            instance = AnthropicProvider(api_key)
        else:
            raise ValueError(f"Unknown provider: {provider}")
        return await instance.verify_key()
