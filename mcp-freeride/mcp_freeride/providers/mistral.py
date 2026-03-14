from __future__ import annotations

from mcp_freeride.providers.base import OpenAICompatibleProvider

MISTRAL_MODELS = [
    "mistral-small-latest",
    "mistral-medium-latest",
    "open-mistral-nemo",
]


class MistralProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str) -> None:
        super().__init__(
            provider_name="mistral",
            base_url="https://api.mistral.ai",
            api_key=api_key,
            default_model="mistral-small-latest",
            available_models=MISTRAL_MODELS,
        )
