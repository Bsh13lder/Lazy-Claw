from __future__ import annotations

import logging

import httpx

from mcp_freeride.providers.base import (
    BaseProvider,
    OpenAICompatibleProvider,
    RateLimitError,
    ProviderError,
)

logger = logging.getLogger(__name__)


class OllamaProvider(OpenAICompatibleProvider):
    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        super().__init__(
            provider_name="ollama",
            base_url=base_url,
            api_key=None,
            default_model="llama3.2",
            available_models=[],
        )

    async def refresh_models(self) -> list[str]:
        """Fetch installed models from Ollama /api/tags endpoint."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url.rstrip('/')}/api/tags")
            if resp.status_code != 200:
                logger.warning("Ollama /api/tags returned %d", resp.status_code)
                return list(self._available_models)
            data = resp.json()
            models_raw = data.get("models", [])
            model_names = [m["name"] for m in models_raw if isinstance(m.get("name"), str)]
            if model_names:
                self._available_models = list(model_names)
                self._default_model = model_names[0]
                logger.info("Ollama models refreshed: %s", model_names)
            return list(self._available_models)
        except Exception as exc:
            logger.warning("Failed to refresh Ollama models: %s", exc)
            return list(self._available_models)

    async def pull_model(self, model_name: str) -> str:
        """Download an Ollama model. POST /api/pull."""
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(
                    f"{self._base_url.rstrip('/')}/api/pull",
                    json={"name": model_name, "stream": False},
                )
            if resp.status_code == 200:
                return f"Model '{model_name}' pulled successfully."
            return f"Pull failed: {resp.status_code} {resp.text[:200]}"
        except httpx.ConnectError:
            return "Ollama is not running. Start it with: ollama serve"
        except httpx.ReadTimeout:
            return f"Pull timed out — model '{model_name}' may be very large. Try pulling manually: ollama pull {model_name}"
        except Exception as exc:
            return f"Pull error: {exc}"

    async def delete_model(self, model_name: str) -> str:
        """Delete an Ollama model. DELETE /api/delete."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(
                    "DELETE",
                    f"{self._base_url.rstrip('/')}/api/delete",
                    json={"name": model_name},
                )
            if resp.status_code == 200:
                return f"Model '{model_name}' deleted."
            if resp.status_code == 404:
                return f"Model '{model_name}' not found."
            return f"Delete failed: {resp.status_code} {resp.text[:200]}"
        except httpx.ConnectError:
            return "Ollama is not running. Start it with: ollama serve"
        except Exception as exc:
            return f"Delete error: {exc}"

    async def show_model(self, model_name: str) -> dict:
        """Get model details. POST /api/show."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._base_url.rstrip('/')}/api/show",
                    json={"name": model_name},
                )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"{resp.status_code} {resp.text[:200]}"}
        except httpx.ConnectError:
            return {"error": "Ollama is not running. Start it with: ollama serve"}
        except Exception as exc:
            return {"error": str(exc)}

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
