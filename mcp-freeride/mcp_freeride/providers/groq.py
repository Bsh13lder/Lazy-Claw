from __future__ import annotations

from mcp_freeride.providers.base import OpenAICompatibleProvider

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]


class GroqProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str) -> None:
        super().__init__(
            provider_name="groq",
            base_url="https://api.groq.com/openai",
            api_key=api_key,
            default_model="llama-3.3-70b-versatile",
            available_models=GROQ_MODELS,
        )
