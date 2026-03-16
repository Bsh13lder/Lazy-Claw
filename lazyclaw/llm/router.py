from __future__ import annotations

from lazyclaw.config import Config
from lazyclaw.llm.providers.base import BaseLLMProvider, LLMMessage, LLMResponse
from lazyclaw.llm.providers.openai_provider import OpenAIProvider
from lazyclaw.llm.providers.anthropic_provider import AnthropicProvider


class LLMRouter:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._providers: dict[str, BaseLLMProvider] = {}

    def _infer_provider_name(self, model: str) -> str:
        if model.startswith(("gpt-", "o1-", "o3-", "o4-")):
            return "openai"
        elif model.startswith("claude-"):
            return "anthropic"
        raise ValueError(f"Cannot infer provider for model: {model}")

    def _get_api_key(self, provider_name: str) -> str | None:
        if provider_name == "openai":
            return self._config.openai_api_key
        elif provider_name == "anthropic":
            return self._config.anthropic_api_key
        return None

    async def _resolve_api_key(self, provider_name: str, user_id: str | None) -> str | None:
        """Get API key from config first, then vault fallback."""
        key = self._get_api_key(provider_name)
        if key:
            return key
        if not user_id:
            return None
        # Try vault
        from lazyclaw.crypto.vault import get_credential
        vault_key = f"{provider_name}_api_key"
        return await get_credential(self._config, user_id, vault_key)

    def _create_provider(self, provider_name: str, api_key: str) -> BaseLLMProvider:
        if provider_name == "openai":
            return OpenAIProvider(api_key)
        elif provider_name == "anthropic":
            return AnthropicProvider(api_key)
        raise ValueError(f"Unknown provider: {provider_name}")

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        user_id: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        model = model or self._config.default_model
        provider_name = self._infer_provider_name(model)

        # Check cache first
        if provider_name not in self._providers:
            api_key = await self._resolve_api_key(provider_name, user_id)
            if not api_key:
                raise ValueError(f"{provider_name.capitalize()} API key not configured. Set it in .env or use vault_set.")
            self._providers[provider_name] = self._create_provider(provider_name, api_key)

        provider = self._providers[provider_name]
        return await provider.chat(messages, model, **kwargs)

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        user_id: str | None = None,
        **kwargs,
    ):
        """Stream chat responses. Yields StreamChunk instances."""
        model = model or self._config.default_model
        provider_name = self._infer_provider_name(model)

        if provider_name not in self._providers:
            api_key = await self._resolve_api_key(provider_name, user_id)
            if not api_key:
                raise ValueError(f"{provider_name.capitalize()} API key not configured.")
            self._providers[provider_name] = self._create_provider(provider_name, api_key)

        provider = self._providers[provider_name]
        async for chunk in provider.stream_chat(messages, model, **kwargs):
            yield chunk

    async def verify_provider(self, provider: str, api_key: str) -> bool:
        instance = self._create_provider(provider, api_key)
        return await instance.verify_key()
