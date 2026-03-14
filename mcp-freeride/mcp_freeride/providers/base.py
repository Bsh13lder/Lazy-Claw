from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    """Raised on HTTP 429 responses."""


class ProviderError(Exception):
    """Raised on non-429 HTTP errors."""


class BaseProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def default_model(self) -> str:
        ...

    @property
    @abstractmethod
    def models(self) -> list[str]:
        ...

    @abstractmethod
    async def chat(self, messages: list[dict], model: str | None = None) -> dict:
        """Send a chat completion request.

        Returns {"content": str, "model": str, "provider": str}.
        """
        ...

    @abstractmethod
    async def is_alive(self) -> bool:
        ...


class OpenAICompatibleProvider(BaseProvider):
    def __init__(
        self,
        provider_name: str,
        base_url: str,
        api_key: str | None,
        default_model: str,
        available_models: list[str],
    ) -> None:
        self._provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._available_models = list(available_models)

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str:
        return self._default_model

    @property
    def models(self) -> list[str]:
        return list(self._available_models)

    async def chat(self, messages: list[dict], model: str | None = None) -> dict:
        used_model = model or self._default_model
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        body = {
            "model": used_model,
            "messages": messages,
            "max_tokens": 4096,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._base_url}/v1/chat/completions",
                headers=headers,
                json=body,
            )

        if response.status_code == 429:
            raise RateLimitError(f"{self.name} rate limited")

        if response.status_code != 200:
            raise ProviderError(
                f"{self.name}: {response.status_code} {response.text}"
            )

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return {"content": content, "model": used_model, "provider": self.name}

    async def is_alive(self) -> bool:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        body = {
            "model": self._default_model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    headers=headers,
                    json=body,
                )
            return response.status_code == 200
        except Exception:
            return False
