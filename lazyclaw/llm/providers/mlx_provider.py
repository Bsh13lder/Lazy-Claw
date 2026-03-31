"""MLX provider — Apple Silicon native local model inference.

DEPRECATED: Ollama 0.19+ includes a native MLX backend for Apple Silicon and
handles model loading, memory management, and process lifecycle automatically.
Use OllamaProvider (ollama_provider.py) with nanbeige4.1:3b instead.

This file is kept for backward compatibility with any users still running
mlx_lm.server manually. It will be removed in a future release.

Legacy usage (manual server required):
  pip install mlx-lm
  mlx_lm.server --model <model> --port 8080
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from lazyclaw.llm.providers.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    StreamChunk,
    ToolCall,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:8080"
_CHAT_TIMEOUT = 300  # seconds — first model load can be slow
_HEALTH_TIMEOUT = 5  # seconds
_STREAM_TIMEOUT = 120  # seconds per chunk


class MLXUnavailableError(Exception):
    """Raised when MLX server is not reachable or returns an error."""


@dataclass(frozen=True)
class MLXModelInfo:
    """Immutable info about a loaded MLX model."""

    model_id: str
    quantization: str  # e.g. "4bit", "8bit", "bf16"


class MLXProvider(BaseLLMProvider):
    """MLX provider using mlx_lm.server's OpenAI-compatible API.

    Usage:
        provider = MLXProvider()  # localhost:8080
        response = await provider.chat(messages, model="qwen3.5-9b")

    The model parameter is passed to the server but mlx_lm.server
    only serves one model at a time — the parameter is used for
    attribution only. Use MLXManager for multi-model swap.
    """

    def __init__(self, base_url: str = _DEFAULT_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._loaded_model: str | None = None  # Cached from /v1/models

    async def _resolve_model(self, model: str) -> str:
        """Get the model name to use in requests.

        Uses the model name from health_check (set at server detection time).
        Does NOT query /v1/models dynamically — that endpoint can return
        a previously-cached model name, causing the server to load the
        wrong model.
        """
        # Use what was set during health_check, or what the caller passes
        return self._loaded_model or model

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
        """Check if MLX server is running. Never raises.

        Note: does NOT set _loaded_model from /v1/models — that endpoint
        can return a cached model name that doesn't match what's actually loaded.
        _loaded_model is set externally by the eco_router based on config.
        """
        try:
            client = await self._get_client()
            resp = await client.get("/v1/models", timeout=_HEALTH_TIMEOUT)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("MLX health check failed: %s", exc)
            return False

    async def get_loaded_model(self) -> str | None:
        """Return the currently loaded model name, or None if unavailable."""
        try:
            client = await self._get_client()
            resp = await client.get("/v1/models", timeout=_HEALTH_TIMEOUT)
            if resp.status_code != 200:
                return None
            data = resp.json()
            models = data.get("data", [])
            if models:
                return models[0].get("id")
            return None
        except Exception:
            return None

    # ── Message serialization (OpenAI format) ─────────────────────────

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
        """Call MLX server with OpenAI-compatible chat completions.

        Thinking mode control:
          - thinking=False → inject /no_think system prompt (fast, for simple chat)
          - thinking=True  → let model think (slower, for complex reasoning)
          - thinking=None  → auto: think for complex, skip for simple
        """
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        thinking = kwargs.pop("thinking", None)

        serialized = [self._serialize_message(m) for m in messages]

        # Use the actual loaded model name (MLX server rejects mismatched names)
        effective_model = await self._resolve_model(model)

        # Auto-thinking: disable for short simple messages, enable for complex
        if thinking is None:
            user_msg = ""
            for m in reversed(messages):
                if m.role == "user":
                    user_msg = m.content
                    break
            # Simple: short message without complex keywords
            thinking = len(user_msg) > 100 or any(
                kw in user_msg.lower() for kw in (
                    "analyze", "compare", "explain", "debug", "plan",
                    "research", "review", "write a blog", "write an article",
                    "code", "implement", "design",
                )
            )

        # Inject /no_think and ensure system message is FIRST
        # Qwen3.5 strictly requires system message at index 0
        _no_think_prefix = "/no_think\nRespond directly without thinking tags.\n"

        # Collect and merge all system messages into one at position 0
        sys_parts = []
        non_sys = []
        for m in serialized:
            if m.get("role") == "system":
                sys_parts.append(m["content"])
            else:
                non_sys.append(m)

        sys_content = "\n".join(sys_parts) if sys_parts else "You are a helpful assistant."
        if not thinking:
            sys_content = _no_think_prefix + sys_content

        serialized = [{"role": "system", "content": sys_content}] + non_sys

        payload: dict = {
            "model": effective_model,
            "messages": serialized,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        # Pass through supported params
        for key in ("temperature", "top_p", "max_tokens", "repetition_penalty"):
            if key in kwargs:
                payload[key] = kwargs[key]

        try:
            client = await self._get_client()
            resp = await client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError as exc:
            raise MLXUnavailableError(
                f"Cannot connect to MLX server at {self._base_url}. "
                "Start it with: mlx_lm.server --model <model>"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body = exc.response.text[:300]
            raise MLXUnavailableError(
                f"MLX server returned HTTP {status}: {body}"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise MLXUnavailableError(
                f"MLX server timed out for model '{model}'. "
                "Model may be loading or system under memory pressure."
            ) from exc
        except Exception as exc:
            err_msg = str(exc).strip() or type(exc).__name__
            raise MLXUnavailableError(f"MLX error: {err_msg}") from exc

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

        # Parse tool calls (OpenAI format)
        parsed_tool_calls = _parse_tool_calls(message.get("tool_calls"))

        # Handle thinking artifacts from local models:
        # - Qwen3.5: puts thinking in "reasoning" field, answer in "content"
        # - Nanbeige: puts <think>...</think> inline in content
        content = (message.get("content") or "").strip()
        reasoning = message.get("reasoning", "")

        # Strip <think>...</think> tags (Nanbeige thinking mode)
        if "<think>" in content:
            import re
            content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()

        # If content is empty but reasoning exists, use reasoning
        if not content and reasoning:
            content = reasoning.strip()

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            usage=usage,
            tool_calls=parsed_tool_calls,
        )

    # ── Streaming ─────────────────────────────────────────────────────

    async def stream_chat(
        self, messages: list[LLMMessage], model: str, **kwargs
    ):
        """Stream chat responses via SSE from MLX server.

        Yields StreamChunk instances. MLX server supports true SSE streaming
        which is critical for responsive UX in Telegram/TUI.
        """
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        thinking = kwargs.pop("thinking", None)

        # Use actual loaded model name
        effective_model = await self._resolve_model(model)

        serialized = [self._serialize_message(m) for m in messages]

        # Auto-thinking (same logic as chat method)
        if thinking is None:
            user_msg = ""
            for m in reversed(messages):
                if m.role == "user":
                    user_msg = m.content
                    break
            thinking = len(user_msg) > 100 or any(
                kw in user_msg.lower() for kw in (
                    "analyze", "compare", "explain", "debug", "plan",
                    "research", "review", "write a blog", "write an article",
                    "code", "implement", "design",
                )
            )

        # Collect and merge all system messages into one at position 0
        _no_think_prefix = "/no_think\nRespond directly without thinking tags.\n"
        sys_parts = []
        non_sys = []
        for m in serialized:
            if m.get("role") == "system":
                sys_parts.append(m["content"])
            else:
                non_sys.append(m)

        sys_content = "\n".join(sys_parts) if sys_parts else "You are a helpful assistant."
        if not thinking:
            sys_content = _no_think_prefix + sys_content

        serialized = [{"role": "system", "content": sys_content}] + non_sys

        payload: dict = {
            "model": effective_model,
            "messages": serialized,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        for key in ("temperature", "top_p", "max_tokens", "repetition_penalty"):
            if key in kwargs:
                payload[key] = kwargs[key]

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(_STREAM_TIMEOUT, connect=10),
            ) as client:
                async with client.stream(
                    "POST", "/v1/chat/completions", json=payload
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        chunk = _parse_sse_line(line, effective_model)
                        if chunk is not None:
                            yield chunk
                            if chunk.done:
                                return
        except httpx.ConnectError:
            yield StreamChunk(
                delta="[MLX server not running]", model=effective_model, done=True
            )
            return
        except Exception as exc:
            # MLX server sometimes 404s on stream but works non-stream.
            # Fallback: call chat() and yield as single chunk.
            logger.warning("MLX stream failed (%s), falling back to non-stream", exc)
            try:
                # Rebuild kwargs for chat() (stream_chat already popped some)
                chat_kwargs = {}
                if tools:
                    chat_kwargs["tools"] = tools
                if tool_choice is not None:
                    chat_kwargs["tool_choice"] = tool_choice
                chat_kwargs["thinking"] = thinking
                for key in ("temperature", "top_p", "max_tokens", "repetition_penalty"):
                    if key in payload:
                        chat_kwargs[key] = payload[key]

                response = await self.chat(messages, model=model, **chat_kwargs)
                yield StreamChunk(
                    delta=response.content,
                    tool_calls=response.tool_calls,
                    usage=response.usage,
                    model=response.model,
                    done=True,
                )
                return
            except Exception as chat_exc:
                logger.warning("MLX non-stream fallback also failed: %s", chat_exc)
                yield StreamChunk(
                    delta=f"[MLX error: {chat_exc}]",
                    model=effective_model, done=True,
                )
                return

        # Final done chunk if not already sent
        yield StreamChunk(model=effective_model, done=True)

    async def verify_key(self) -> bool:
        """MLX has no API key — just check health."""
        return await self.health_check()


# ── Helpers ───────────────────────────────────────────────────────────


def _parse_tool_calls(raw_tcs: list[dict] | None) -> list[ToolCall] | None:
    """Parse OpenAI-format tool calls from response message."""
    if not raw_tcs:
        return None

    parsed = []
    for tc in raw_tcs:
        func = tc.get("function", {})
        args = func.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        parsed.append(
            ToolCall(
                id=tc.get("id", ""),
                name=func.get("name", ""),
                arguments=args,
            )
        )
    return parsed


def _parse_sse_line(line: str, fallback_model: str) -> StreamChunk | None:
    """Parse a single SSE line into a StreamChunk, or None if not data."""
    if not line.startswith("data: "):
        return None

    payload = line[6:].strip()
    if payload == "[DONE]":
        return StreamChunk(model=fallback_model, done=True)

    try:
        chunk_data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    choices = chunk_data.get("choices", [])
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    content = delta.get("content", "")
    finish_reason = choice.get("finish_reason")

    # Parse streaming tool calls if present
    tool_calls = _parse_tool_calls(delta.get("tool_calls"))

    # Usage comes in the final chunk (after finish_reason)
    usage = None
    raw_usage = chunk_data.get("usage")
    if raw_usage:
        usage = {
            "prompt_tokens": raw_usage.get("prompt_tokens", 0),
            "completion_tokens": raw_usage.get("completion_tokens", 0),
            "total_tokens": raw_usage.get("total_tokens", 0),
        }

    return StreamChunk(
        delta=content,
        tool_calls=tool_calls,
        usage=usage,
        model=chunk_data.get("model", fallback_model),
        done=finish_reason is not None,
    )
