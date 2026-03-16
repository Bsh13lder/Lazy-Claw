from __future__ import annotations

import httpx

from mcp_freeride.providers.base import (
    BaseProvider,
    OpenAICompatibleProvider,
    RateLimitError,
    ProviderError,
)

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

    async def chat(self, messages: list[dict], model: str | None = None) -> dict:
        """Use Ollama's native /api/chat endpoint (not OpenAI /v1)."""
        used_model = model or self._default_model
        body = {
            "model": used_model,
            "messages": messages,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{self._base_url}/api/chat", json=body)
        if resp.status_code == 429:
            raise RateLimitError("ollama rate limited")
        if resp.status_code != 200:
            raise ProviderError(f"ollama: {resp.status_code} {resp.text}")
        data = resp.json()
        # Non-stream response shape: { 'message': {'role': 'assistant', 'content': '...'}, ... }
        content = data.get("message", {}).get("content", "")
        return {"content": content, "model": used_model, "provider": self.name}

    async def is_alive(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False
