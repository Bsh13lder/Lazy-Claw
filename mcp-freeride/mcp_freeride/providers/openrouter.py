from __future__ import annotations

from mcp_freeride.providers.base import OpenAICompatibleProvider

OPENROUTER_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "google/gemma-3-27b-it:free",
    "qwen/qwen3-235b-a22b:free",
    "deepseek/deepseek-r1-0528:free",
]


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str) -> None:
        super().__init__(
            provider_name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_model="meta-llama/llama-3.3-70b-instruct:free",
            available_models=OPENROUTER_MODELS,
        )
