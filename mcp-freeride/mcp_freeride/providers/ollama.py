from __future__ import annotations

import httpx

from mcp_freeride.providers.base import OpenAICompatibleProvider

OLLAMA_MODELS = [
    "llama3.2",
    "llama3.1",
    "mistral",
    "phi3",
    "gemma2",
]


class OllamaProvider(OpenAICompatibleProvider):
    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        super().__init__(
            provider_name="ollama",
            base_url=base_url,
            api_key=None,
            default_model="llama3.2",
            available_models=OLLAMA_MODELS,
        )

    async def is_alive(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False
