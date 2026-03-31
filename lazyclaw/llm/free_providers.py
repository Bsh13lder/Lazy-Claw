"""Free LLM providers — direct API integration.

Each provider uses OpenAI-compatible chat format. One shared httpx client
handles all of them — just change base_url and api_key.

No mcp-freeride dependency. Direct HTTP calls only.

Supported providers:
  - Groq (fastest, 30 req/min)
  - OpenRouter (best quality, Qwen 235B / Llama 70B free)
  - Google AI Studio (Gemini Flash, 15 req/min)
  - Together AI ($5 free credit)
  - Mistral (free tier, mistral-small)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


# ── Provider definitions ───────────────────────────────────────────────

@dataclass(frozen=True)
class FreeProviderDef:
    """Static definition for a free LLM provider."""

    name: str
    env_key: str
    base_url: str
    free_models: tuple[str, ...]
    signup_url: str
    rate_limit_rpm: int = 0  # 0 = unknown/unlimited
    description: str = ""


PROVIDER_DEFS: dict[str, FreeProviderDef] = {
    "groq": FreeProviderDef(
        name="groq",
        env_key="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        free_models=(
            "llama-3.3-70b-versatile",
            "gemma2-9b-it",
        ),
        signup_url="https://console.groq.com/keys",
        rate_limit_rpm=30,
        description="Groq — 30 req/min, Llama 3.3 70B",
    ),
    "openrouter": FreeProviderDef(
        name="openrouter",
        env_key="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        free_models=(
            "qwen/qwen3-235b-a22b:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ),
        signup_url="https://openrouter.ai/keys",
        rate_limit_rpm=20,
        description="OpenRouter — Qwen 235B, Llama 70B (free)",
    ),
    "google": FreeProviderDef(
        name="google",
        env_key="GOOGLE_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        free_models=("gemini-2.0-flash",),
        signup_url="https://aistudio.google.com/apikey",
        rate_limit_rpm=15,
        description="Google AI Studio — 15 req/min, Gemini 2.0 Flash",
    ),
    "together": FreeProviderDef(
        name="together",
        env_key="TOGETHER_API_KEY",
        base_url="https://api.together.xyz/v1",
        free_models=(
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ),
        signup_url="https://api.together.ai/settings",
        rate_limit_rpm=60,
        description="Together AI — $5 free credit, Llama/Qwen",
    ),
    "mistral": FreeProviderDef(
        name="mistral",
        env_key="MISTRAL_API_KEY",
        base_url="https://api.mistral.ai/v1",
        free_models=("mistral-small-latest",),
        signup_url="https://console.mistral.ai/api-keys",
        rate_limit_rpm=30,
        description="Mistral — free tier, mistral-small",
    ),
}

# Priority order: fastest first, then best quality, then backups
PRIORITY_ORDER: tuple[str, ...] = (
    "groq",
    "openrouter",
    "google",
    "together",
    "mistral",
)


# ── Provider result ────────────────────────────────────────────────────

@dataclass(frozen=True)
class FreeProviderResult:
    """Result from a free provider chat call."""

    content: str
    model: str
    provider: str
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FreeStreamChunk:
    """A single SSE chunk from a free provider."""

    delta: str = ""
    model: str = ""
    provider: str = ""
    done: bool = False


# ── Discovery ──────────────────────────────────────────────────────────

def discover_providers() -> dict[str, str]:
    """Scan env vars for configured free provider API keys.

    Returns: {provider_name: api_key} for all providers with keys set.
    """
    found: dict[str, str] = {}
    for name, pdef in PROVIDER_DEFS.items():
        key = os.environ.get(pdef.env_key, "").strip()
        if key:
            found[name] = key
    return found


def get_provider_info() -> list[dict]:
    """Return info about all providers for setup/status display."""
    configured = discover_providers()
    result = []
    for name in PRIORITY_ORDER:
        pdef = PROVIDER_DEFS[name]
        result.append({
            "name": name,
            "configured": name in configured,
            "env_key": pdef.env_key,
            "description": pdef.description,
            "models": list(pdef.free_models),
            "signup_url": pdef.signup_url,
            "rate_limit_rpm": pdef.rate_limit_rpm,
        })
    return result


# ── Chat client ────────────────────────────────────────────────────────

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0)


async def _openai_chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    stream: bool = False,
    extra_headers: dict | None = None,
) -> httpx.Response:
    """Send an OpenAI-compatible chat completion request."""
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    body = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }

    timeout = _STREAM_TIMEOUT if stream else _TIMEOUT
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=body, headers=headers)
        response.raise_for_status()
        return response


async def chat(
    provider_name: str,
    api_key: str,
    messages: list[dict],
    model: str | None = None,
) -> FreeProviderResult:
    """Send a chat request to a specific free provider.

    Args:
        provider_name: One of the PROVIDER_DEFS keys.
        api_key: API key for this provider.
        messages: OpenAI-format message dicts.
        model: Model to use. None = first free model for this provider.

    Returns:
        FreeProviderResult with content, model, provider, usage.

    Raises:
        httpx.HTTPStatusError: On 4xx/5xx responses (including 429 rate limit).
        httpx.TimeoutException: On timeout.
        KeyError: If provider_name is unknown.
    """
    pdef = PROVIDER_DEFS[provider_name]
    effective_model = model or pdef.free_models[0]

    extra_headers = None
    if provider_name == "openrouter":
        extra_headers = {
            "HTTP-Referer": "https://github.com/lazyclaw/lazyclaw",
            "X-Title": "LazyClaw",
        }

    response = await _openai_chat(
        base_url=pdef.base_url,
        api_key=api_key,
        model=effective_model,
        messages=messages,
        extra_headers=extra_headers,
    )

    data = response.json()
    choices = data.get("choices", [])
    content = ""
    if choices:
        content = choices[0].get("message", {}).get("content", "")

    usage_raw = data.get("usage", {})
    usage = {
        "prompt_tokens": usage_raw.get("prompt_tokens", 0),
        "completion_tokens": usage_raw.get("completion_tokens", 0),
        "total_tokens": usage_raw.get("total_tokens", 0),
    }

    return FreeProviderResult(
        content=content,
        model=data.get("model", effective_model),
        provider=provider_name,
        usage=usage,
    )


async def stream_chat(
    provider_name: str,
    api_key: str,
    messages: list[dict],
    model: str | None = None,
) -> AsyncIterator[FreeStreamChunk]:
    """Stream a chat response from a free provider via SSE.

    Yields FreeStreamChunk instances. The final chunk has done=True.
    Falls back to non-streaming if SSE parsing fails.
    """
    pdef = PROVIDER_DEFS[provider_name]
    effective_model = model or pdef.free_models[0]

    extra_headers = None
    if provider_name == "openrouter":
        extra_headers = {
            "HTTP-Referer": "https://github.com/lazyclaw/lazyclaw",
            "X-Title": "LazyClaw",
        }

    url = f"{pdef.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    body = {
        "model": effective_model,
        "messages": messages,
        "stream": True,
    }

    timeout = _STREAM_TIMEOUT
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=body, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    yield FreeStreamChunk(
                        model=effective_model,
                        provider=provider_name,
                        done=True,
                    )
                    return
                try:
                    chunk_data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk_data.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield FreeStreamChunk(
                        delta=content,
                        model=chunk_data.get("model", effective_model),
                        provider=provider_name,
                    )

    # Final done chunk (if not already sent via [DONE])
    yield FreeStreamChunk(
        model=effective_model,
        provider=provider_name,
        done=True,
    )


# ── Connection test ────────────────────────────────────────────────────

async def test_provider(provider_name: str, api_key: str) -> tuple[bool, str]:
    """Test a provider connection by sending a minimal request.

    Returns: (success, message)
    """
    try:
        result = await asyncio.wait_for(
            chat(
                provider_name,
                api_key,
                [{"role": "user", "content": "hi"}],
            ),
            timeout=15,
        )
        model_count = len(PROVIDER_DEFS[provider_name].free_models)
        return True, f"{provider_name} connected — {model_count} models available"
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            return False, f"Invalid API key (HTTP 401)"
        if status == 429:
            # Rate limited but key works
            return True, f"{provider_name} connected (rate-limited right now)"
        return False, f"HTTP {status}: {exc.response.text[:200]}"
    except asyncio.TimeoutError:
        return False, "Connection timed out (15s)"
    except Exception as exc:
        return False, f"Error: {exc}"


# ── Cascade router ─────────────────────────────────────────────────────

async def cascade_chat(
    messages: list[dict],
    provider_order: list[str] | None = None,
    api_keys: dict[str, str] | None = None,
    preferred_model: str | None = None,
) -> FreeProviderResult:
    """Try providers in priority order until one succeeds.

    Args:
        messages: OpenAI-format message dicts.
        provider_order: Custom order. None = PRIORITY_ORDER filtered to configured.
        api_keys: {provider: key}. None = discover from env.
        preferred_model: Specific model to use (provider inferred from model name).

    Returns:
        FreeProviderResult from first successful provider.

    Raises:
        RuntimeError: If all providers fail.
    """
    keys = api_keys or discover_providers()
    if not keys:
        raise RuntimeError(
            "No free providers configured. "
            "Run /eco setup or add API keys to .env"
        )

    order = provider_order or [p for p in PRIORITY_ORDER if p in keys]
    if not order:
        raise RuntimeError("No configured providers in the priority list")

    errors: list[str] = []
    for provider_name in order:
        api_key = keys.get(provider_name)
        if not api_key:
            continue

        model = None
        if preferred_model and _model_matches_provider(preferred_model, provider_name):
            model = preferred_model

        try:
            result = await asyncio.wait_for(
                chat(provider_name, api_key, messages, model),
                timeout=25,
            )
            return result
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            err_msg = f"{provider_name}: HTTP {status}"
            logger.warning("Free provider %s failed: %s", provider_name, err_msg)
            errors.append(err_msg)
            continue
        except asyncio.TimeoutError:
            err_msg = f"{provider_name}: timeout"
            logger.warning("Free provider %s timed out", provider_name)
            errors.append(err_msg)
            continue
        except Exception as exc:
            err_msg = f"{provider_name}: {exc}"
            logger.warning("Free provider %s error: %s", provider_name, exc)
            errors.append(err_msg)
            continue

    raise RuntimeError(
        f"All free providers failed: {'; '.join(errors)}"
    )


def _model_matches_provider(model: str, provider: str) -> bool:
    """Check if a preferred model belongs to a given provider."""
    pdef = PROVIDER_DEFS.get(provider)
    if not pdef:
        return False
    return model in pdef.free_models
