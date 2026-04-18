"""Browser ask_vision action — delegate visual understanding to a local vision model.

When the brain (MiniMax, coder models, etc.) can't see images, it calls
`browser(action="ask_vision", question="...")`. We capture the current screenshot,
send it to `gemma4:e2b` via Ollama with `keep_alive=5m` (model unloads after idle),
and return the text answer. UI/Telegram still receive the raw screenshot via
the ToolResult attachment.

Vision-capable brains (Claude, Gemini) don't need this — they should call
`screenshot` directly once that path delivers images to the LLM.
"""

from __future__ import annotations

import base64
import logging
import os
import time

import httpx

from lazyclaw.runtime.tool_result import Attachment, ToolResult

from .backends import get_backend

logger = logging.getLogger(__name__)

_OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
_VISION_MODEL = os.environ.get("LAZYCLAW_VISION_MODEL", "gemma4:e2b")
_KEEP_ALIVE = "5m"  # Ollama unloads the model after this idle window
_REQUEST_TIMEOUT = 120.0  # cold-start inference can be slow on 8GB RAM

_SYSTEM_PROMPT = (
    "You are a precise vision assistant helping a web-navigation agent. "
    "Answer ONLY the question asked, based strictly on what's visible in the "
    "screenshot. Be concise. If the answer isn't visible, say so explicitly."
)

# Per-question URL-scoped cache — consecutive identical queries on the same
# page hit the cache instead of re-running inference.
_cache: dict[tuple[str, str], tuple[float, str]] = {}
_CACHE_TTL_SECONDS = 30.0
_CACHE_MAX_ENTRIES = 64


def _prune_cache(now: float) -> None:
    if len(_cache) <= _CACHE_MAX_ENTRIES:
        return
    cutoff = now - _CACHE_TTL_SECONDS
    stale = [k for k, (ts, _) in _cache.items() if ts < cutoff]
    for k in stale:
        _cache.pop(k, None)


async def action_ask_vision(
    user_id: str, params: dict, tab_context,
) -> ToolResult:
    """Delegate a visual question about the current page to a local vision model."""
    question = (params.get("question") or "").strip()
    if not question:
        return ToolResult(
            text=(
                "ask_vision needs a 'question' parameter. Ask something specific, "
                "e.g. 'is the submit button enabled?' or 'what error is the modal showing?'"
            ),
        )

    backend = await get_backend(user_id, tab_context)
    try:
        url = await backend.current_url()
    except Exception:
        url = ""

    cache_key = (url, question)
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return ToolResult(text=f"(cached) {cached[1]}")

    try:
        png_bytes = await backend.screenshot()
    except Exception as exc:
        logger.warning("ask_vision screenshot failed: %s", exc)
        return ToolResult(text=f"Could not capture screenshot: {exc}")

    attachment = Attachment(
        data=png_bytes,
        media_type="image/png",
        filename="screenshot.png",
    )

    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": _VISION_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": question, "images": [b64]},
        ],
        "stream": False,
        "keep_alive": _KEEP_ALIVE,
    }

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(f"{_OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        return ToolResult(
            text=(
                f"Vision model unavailable — cannot reach Ollama at {_OLLAMA_URL}. "
                f"Start it with: ollama serve"
            ),
            attachments=(attachment,),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return ToolResult(
                text=(
                    f"Vision model '{_VISION_MODEL}' not installed. "
                    f"Install: ollama pull {_VISION_MODEL}"
                ),
                attachments=(attachment,),
            )
        return ToolResult(
            text=f"Vision model error: HTTP {exc.response.status_code}",
            attachments=(attachment,),
        )
    except httpx.ReadTimeout:
        return ToolResult(
            text=(
                f"Vision model timed out after {_REQUEST_TIMEOUT:.0f}s "
                f"(cold start on first call — try again in a moment)"
            ),
            attachments=(attachment,),
        )
    except Exception as exc:
        logger.warning("ask_vision call failed: %s", exc, exc_info=True)
        return ToolResult(
            text=f"Vision call failed: {exc}",
            attachments=(attachment,),
        )

    answer = (data.get("message") or {}).get("content", "").strip()
    if not answer:
        answer = "(vision model returned no answer)"

    _cache[cache_key] = (now, answer)
    _prune_cache(now)

    return ToolResult(text=answer, attachments=(attachment,))
