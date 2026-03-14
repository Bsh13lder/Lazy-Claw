from __future__ import annotations

from mcp_freeride.providers.base import OpenAICompatibleProvider

GEMINI_MODELS = [
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


class GeminiProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str) -> None:
        super().__init__(
            provider_name="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=api_key,
            default_model="gemini-2.5-flash-preview-05-20",
            available_models=GEMINI_MODELS,
        )
