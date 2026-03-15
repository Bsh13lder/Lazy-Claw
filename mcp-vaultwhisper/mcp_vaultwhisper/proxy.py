"""Lightweight free AI proxy with provider fallback."""
from __future__ import annotations

import logging

import httpx

from mcp_vaultwhisper.config import VaultWhisperConfig

logger = logging.getLogger(__name__)

KNOWN_PROVIDERS: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai", "llama-3.3-70b-versatile"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
    "openrouter": ("https://openrouter.ai/api", "meta-llama/llama-3.3-70b-instruct:free"),
    "together": ("https://api.together.xyz", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "mistral": ("https://api.mistral.ai", "mistral-small-latest"),
    "huggingface": (
        "https://api-inference.huggingface.co/models/meta-llama/Llama-3.3-70B-Instruct",
        "meta-llama/Llama-3.3-70B-Instruct",
    ),
    "ollama": ("http://localhost:11434", "llama3.2"),
}

API_KEY_MAP: dict[str, str] = {
    "groq": "groq_api_key",
    "gemini": "gemini_api_key",
    "openrouter": "openrouter_api_key",
    "together": "together_api_key",
    "mistral": "mistral_api_key",
    "huggingface": "hf_api_key",
}


def get_provider_order(config: VaultWhisperConfig) -> list[tuple[str, str, str, str | None]]:
    """Return ordered list of (name, base_url, model, api_key) for configured providers."""
    providers: list[tuple[str, str, str, str | None]] = []
    for name, (base_url, default_model) in KNOWN_PROVIDERS.items():
        if name == "ollama":
            providers.append((name, config.ollama_url, default_model, None))
            continue
        attr = API_KEY_MAP.get(name)
        if attr:
            key = getattr(config, attr, None)
            if key:
                providers.append((name, base_url, default_model, key))
    return providers


async def proxy_chat(config: VaultWhisperConfig, messages: list[dict]) -> dict:
    """Send messages to the first available free AI provider with fallback."""
    providers = get_provider_order(config)
    if not providers:
        return {"error": "No AI providers configured. Set API keys in environment."}

    for name, base_url, model, api_key in providers:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body = {"model": model, "messages": messages, "max_tokens": 4096}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{base_url.rstrip('/')}/v1/chat/completions",
                    headers=headers,
                    json=body,
                )
            if resp.status_code == 429:
                logger.warning("Rate limited by %s, trying next", name)
                continue
            if resp.status_code != 200:
                logger.warning("Provider %s returned %d, trying next", name, resp.status_code)
                continue
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return {"content": content, "provider": name, "model": model}
        except Exception as exc:
            logger.warning("Provider %s failed: %s, trying next", name, exc)
            continue

    return {"error": "All free AI providers failed. Check API keys and try again."}
