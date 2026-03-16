from __future__ import annotations

import httpx

from mcp_freeride.providers.base import (
    OpenAICompatibleProvider,
    ProviderError,
    RateLimitError,
)

HF_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "microsoft/Phi-3-mini-4k-instruct",
]


class HuggingFaceProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(
            provider_name="huggingface",
            base_url="https://router.huggingface.co/hf-inference",
            api_key=api_key,
            default_model="meta-llama/Llama-3.3-70B-Instruct",
            available_models=HF_MODELS,
        )

    def _build_url(self, model: str) -> str:
        """HuggingFace uses the router API."""
        return f"https://router.huggingface.co/hf-inference/v1/chat/completions"

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        used_model = model or self._default_model
        if used_model not in self._available_models:
            raise ProviderError(
                f"Model {used_model!r} not available. "
                f"Choose from: {self._available_models}"
            )

        url = self._build_url(used_model)
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": used_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code == 429:
            raise RateLimitError(f"HuggingFace rate limit hit for {used_model}")
        if resp.status_code != 200:
            raise ProviderError(
                f"HuggingFace API error {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return {"content": content, "model": used_model, "provider": self.name}
