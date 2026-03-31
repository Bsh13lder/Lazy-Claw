"""Ollama provider — OpenAI-compatible local model inference.

Uses Ollama's /v1/chat/completions endpoint (OpenAI-compatible format).
Tool calling format is identical to OpenAI — no special handling needed.
All errors are caught gracefully; Ollama being down never crashes the agent.

Think-tag handling:
  - Nanbeige: outputs <think>...</think> inline in content — stripped here.
  - Qwen models: /no_think prefix injected into system prompt to suppress thinking.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from lazyclaw.llm.providers.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    ToolCall,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_CHAT_TIMEOUT = 300  # seconds (first model load can be slow on 8GB RAM)
_HEALTH_TIMEOUT = 5  # seconds

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_NO_THINK_PREFIX = "/no_think\nRespond directly without thinking tags.\n"

# Models that output <think> tags and need them stripped
_THINK_TAG_MODELS = frozenset({"nanbeige4.1:3b", "nanbeige4.1"})

# Models that accept /no_think prefix to suppress thinking entirely
_NO_THINK_MODELS = frozenset({"qwen3.5:9b", "qwen3:0.6b", "qwen3:1.7b", "qwen3.5"})


def _strip_think_tags(content: str) -> str:
    """Remove <think>...</think> blocks from model output."""
    return _THINK_RE.sub("", content).strip()


class OllamaUnavailableError(Exception):
    """Raised when Ollama is not reachable or returns an error."""


class OllamaProvider(BaseLLMProvider):
    """Ollama provider using the OpenAI-compatible /v1/ API."""

    def __init__(self, base_url: str = _DEFAULT_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(_CHAT_TIMEOUT, connect=10),
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Health check ──────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check if Ollama is running. Never raises."""
        try:
            client = await self._get_client()
            resp = await client.get("/api/tags", timeout=_HEALTH_TIMEOUT)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("Ollama health check failed: %s", exc)
            return False

    async def list_running(self) -> list[dict]:
        """List currently loaded models. Returns [] on error.

        Each dict: {"name": str, "size_mb": int}
        """
        try:
            client = await self._get_client()
            resp = await client.get("/api/ps", timeout=_HEALTH_TIMEOUT)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [
                {
                    "name": m.get("name", "unknown"),
                    "size_mb": m.get("size", 0) // (1024 * 1024),
                }
                for m in data.get("models", [])
            ]
        except Exception as exc:
            logger.debug("Ollama list_running failed: %s", exc)
            return []

    # ── Message serialization (same as OpenAI) ────────────────────────

    @staticmethod
    def _serialize_message(m: LLMMessage) -> dict:
        if m.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": m.tool_call_id,
                "content": m.content,
            }
        if m.role == "assistant" and m.tool_calls:
            return {
                "role": "assistant",
                "content": m.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ],
            }
        return {"role": m.role, "content": m.content}

    # ── Chat ──────────────────────────────────────────────────────────

    async def chat(
        self, messages: list[LLMMessage], model: str, **kwargs
    ) -> LLMResponse:
        """Call Ollama with OpenAI-compatible chat completions.

        Think-mode control:
          - Qwen models: /no_think injected into system prompt (suppress thinking)
          - Nanbeige: <think> tags stripped from response content
        """
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)

        serialized = [self._serialize_message(m) for m in messages]

        # Inject /no_think for Qwen models to suppress reasoning output
        model_base = model.split(":")[0] if ":" in model else model
        if model in _NO_THINK_MODELS or model_base in _NO_THINK_MODELS:
            sys_parts = [m["content"] for m in serialized if m.get("role") == "system"]
            non_sys = [m for m in serialized if m.get("role") != "system"]
            sys_content = "\n".join(sys_parts) if sys_parts else "You are a helpful assistant."
            sys_content = _NO_THINK_PREFIX + sys_content
            serialized = [{"role": "system", "content": sys_content}] + non_sys

        payload: dict = {
            "model": model,
            "messages": serialized,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        try:
            client = await self._get_client()
            resp = await client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(
                "Cannot connect to Ollama at "
                f"{self._base_url}. Is it running?"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise OllamaUnavailableError(
                    f"Model '{model}' not found in Ollama. "
                    f"Install it with: ollama pull {model}"
                ) from exc
            raise OllamaUnavailableError(
                f"Ollama returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise OllamaUnavailableError(
                f"Ollama timed out loading model '{model}' "
                f"(may need more RAM or first load is slow)"
            ) from exc
        except Exception as exc:
            err_msg = str(exc).strip()
            if not err_msg:
                err_msg = type(exc).__name__
            raise OllamaUnavailableError(f"Ollama error: {err_msg}") from exc

        choice = data["choices"][0]
        message = choice["message"]

        # Parse usage
        usage = None
        raw_usage = data.get("usage")
        if raw_usage:
            usage = {
                "prompt_tokens": raw_usage.get("prompt_tokens", 0),
                "completion_tokens": raw_usage.get("completion_tokens", 0),
                "total_tokens": raw_usage.get("total_tokens", 0),
            }

        # Parse tool calls (same format as OpenAI)
        parsed_tool_calls = None
        raw_tcs = message.get("tool_calls")
        if raw_tcs:
            parsed_tool_calls = []
            for tc in raw_tcs:
                func = tc.get("function", {})
                args = func.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                parsed_tool_calls.append(
                    ToolCall(
                        id=tc.get("id", ""),
                        name=func.get("name", ""),
                        arguments=args,
                    )
                )

        content = message.get("content") or ""

        # Strip <think>...</think> tags — Nanbeige and other thinking models
        # output these inline when thinking mode is not suppressed via /no_think
        if "<think>" in content:
            content = _strip_think_tags(content)

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            usage=usage,
            tool_calls=parsed_tool_calls,
        )

    # ── Streaming (uses base class non-streaming fallback for now) ────
    # Ollama supports streaming but for initial implementation we use
    # the BaseLLMProvider default: call chat() and yield a single chunk.

    async def verify_key(self) -> bool:
        """Ollama has no API key — just check health."""
        return await self.health_check()
