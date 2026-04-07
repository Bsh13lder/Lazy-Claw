"""Simple AI client that calls free AI APIs for task intelligence."""
from __future__ import annotations

import json
import logging

import httpx

from mcp_taskai.config import TaskAIConfig

logger = logging.getLogger(__name__)

PROVIDER_CONFIGS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "key_field": "groq_api_key",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-2.0-flash",
        "key_field": "gemini_api_key",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "key_field": "openrouter_api_key",
    },
}


class AllProvidersFailedError(Exception):
    """Raised when all AI providers fail to respond."""


class AIClient:
    """Routes AI requests through free providers with fallback."""

    def __init__(self, config: TaskAIConfig) -> None:
        self._config = config

    async def complete(self, prompt: str) -> str:
        """Send a prompt to the first available provider and return text."""
        errors: list[str] = []
        for provider_name in self._config.provider_names:
            if provider_name == "none":
                continue
            provider = PROVIDER_CONFIGS.get(provider_name)
            if not provider:
                continue
            api_key = getattr(self._config, provider["key_field"], None)
            if not api_key:
                continue
            try:
                return await self._call_provider(
                    url=provider["url"],
                    model=provider["model"],
                    api_key=api_key,
                    prompt=prompt,
                )
            except Exception as exc:
                logger.warning("Provider %s failed: %s", provider_name, exc)
                errors.append(f"{provider_name}: {exc}")

        raise AllProvidersFailedError(
            f"All providers failed: {'; '.join(errors)}" if errors
            else "No providers configured"
        )

    async def complete_json(self, prompt: str) -> dict:
        """Send a prompt and parse the response as JSON."""
        text = await self.complete(prompt)
        # Extract JSON from response (handle markdown code blocks)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON object in text
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(cleaned[start:end])
            return {"raw": text, "parse_error": True}

    async def _call_provider(
        self, url: str, model: str, api_key: str, prompt: str,
    ) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
            "temperature": 0.3,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
            choices = body.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            raise ValueError(f"No choices in response: {body}")
