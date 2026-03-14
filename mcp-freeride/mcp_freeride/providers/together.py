from __future__ import annotations

from mcp_freeride.providers.base import OpenAICompatibleProvider

TOGETHER_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
]


class TogetherProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str) -> None:
        super().__init__(
            provider_name="together",
            base_url="https://api.together.xyz",
            api_key=api_key,
            default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
            available_models=TOGETHER_MODELS,
        )
