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
        elif model.startswith(("gemma", "llama", "qwen", "phi", "mistral", "lazyclaw-")):
            return "ollama"
        elif model.startswith(("MiniMax-", "minimax-")):
            return "minimax"
        # Check MODEL_CATALOG for known local models
        from lazyclaw.llm.model_registry import get_model
        profile = get_model(model)
        if profile and profile.provider == "ollama":
            return "ollama"
        if profile and profile.provider == "minimax":
            return "minimax"
        raise ValueError(f"Cannot infer provider for model: {model}")

    def _get_api_key(self, provider_name: str) -> str | None:
        if provider_name == "openai":
            return self._config.openai_api_key
        elif provider_name == "anthropic":
            return self._config.anthropic_api_key
        elif provider_name == "minimax":
            return self._config.minimax_api_key
        return None

    _PROVIDER_VAULT_KEY: dict[str, str] = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "minimax": "MINIMAX_API_KEY",
    }

    _vault_key_cache: dict[str, tuple[str, float]] = {}
    _VAULT_CACHE_TTL = 300.0  # 5 minutes

    async def _resolve_api_key(self, provider_name: str, user_id: str | None) -> str | None:
        """Get API key from config first, then vault fallback (with timeout + cache)."""
        key = self._get_api_key(provider_name)
        if key:
            return key
        if not user_id:
            return None

        import time
        vault_key = self._PROVIDER_VAULT_KEY.get(provider_name, f"{provider_name}_api_key")
        cache_entry = self._vault_key_cache.get(vault_key)
        if cache_entry is not None:
            cached_key, cached_at = cache_entry
            if time.monotonic() - cached_at < self._VAULT_CACHE_TTL:
                return cached_key

        try:
            import asyncio
            from lazyclaw.crypto.vault import get_credential
            key = await asyncio.wait_for(
                get_credential(self._config, user_id, vault_key),
                timeout=5.0,
            )
            if key:
                self._vault_key_cache[vault_key] = (key, time.monotonic())
            return key
        except (asyncio.TimeoutError, Exception):
            return None

    def _create_provider(self, provider_name: str, api_key: str) -> BaseLLMProvider:
        if provider_name == "openai":
            return OpenAIProvider(api_key)
        elif provider_name == "anthropic":
            return AnthropicProvider(api_key)
        elif provider_name == "ollama":
            from lazyclaw.llm.providers.ollama_provider import OllamaProvider
            return OllamaProvider()
        elif provider_name == "minimax":
            # MiniMax runs through their Anthropic-compatible endpoint — they
            # recommend it over OpenAI-compat for full system/tool/thinking
            # support. Same API key works on both compat layers.
            return AnthropicProvider(
                api_key=api_key,
                base_url=self._config.minimax_base_url,
                disable_prompt_cache=True,
                default_model="MiniMax-M2.7",
            )
        raise ValueError(f"Unknown provider: {provider_name}")

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        user_id: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        model = model or self._config.brain_model
        provider_name = self._infer_provider_name(model)

        # Ollama doesn't need an API key — skip key check
        if provider_name == "ollama" and provider_name not in self._providers:
            self._providers[provider_name] = self._create_provider(provider_name, "")

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
        model = model or self._config.brain_model
        provider_name = self._infer_provider_name(model)

        # Ollama doesn't need an API key
        if provider_name == "ollama" and provider_name not in self._providers:
            self._providers[provider_name] = self._create_provider(provider_name, "")

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
