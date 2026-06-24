"""Image generation via MiniMax's image-01 API.

Sibling of ``text_to_speech.py``: takes a prompt and produces image(s), then
delivers them the same way the ``send_*`` file-producing skills do — Telegram
document attachments when Telegram is configured, with a clear message when it
isn't.

API (official MiniMax intl host, June 2026)::

    POST https://api.minimax.io/v1/image_generation
    Authorization: Bearer <MINIMAX_API_KEY>
    Content-Type: application/json

The key comes from the ``MINIMAX_API_KEY`` env var — the same one the LLM
router uses, so no new secret config. No GroupId on the current intl host.

Request body::

    {"model": "image-01", "prompt": "<str>", "aspect_ratio": "1:1",
     "n": 1, "response_format": "url", "prompt_optimizer": true}

Synchronous — one POST returns finished image(s) under ``data.image_urls``
(URLs expire ~24h, so we download the bytes and deliver them, not the link).
ALWAYS check ``base_resp.status_code == 0`` — a 200 with a non-zero
``base_resp.status_code`` is still a failure (auth / quota / sensitive content).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_API_URL = "https://api.minimax.io/v1/image_generation"
_API_KEY_ENV = "MINIMAX_API_KEY"

_DEFAULT_MODEL = "image-01"
_DEFAULT_ASPECT_RATIO = "1:1"
_ALLOWED_ASPECT_RATIOS = (
    "1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9",
)
_MAX_PROMPT_CHARS = 1500
_MIN_N = 1
_MAX_N = 9
_HTTP_TIMEOUT_S = 120.0


class GenerateImageSkill(BaseSkill):
    """Generate image(s) from a text prompt via MiniMax and deliver them."""

    def __init__(self, config: Any = None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def display_name(self) -> str:
        return "Generate Image"

    @property
    def description(self) -> str:
        return (
            "Generate image(s) from a text prompt via MiniMax (e.g. 'make a "
            "picture of...', 'create a logo', 'draw...'). Delivers PNG(s) as "
            "Telegram documents when Telegram is configured. Choose an "
            "aspect_ratio and how many images (n, 1-9)."
        )

    @property
    def category(self) -> str:
        # `utility` is ALLOW by default (permissions/models.py). Runs against
        # the user's own MiniMax key and only returns generated images to them.
        return "utility"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        f"What to generate (required, up to {_MAX_PROMPT_CHARS} "
                        "characters)."
                    ),
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": list(_ALLOWED_ASPECT_RATIOS),
                    "description": (
                        f"Image shape (default '{_DEFAULT_ASPECT_RATIO}')."
                    ),
                },
                "n": {
                    "type": "integer",
                    "description": (
                        f"How many images, {_MIN_N}-{_MAX_N} (default {_MIN_N})."
                    ),
                },
            },
            "required": ["prompt"],
        }

    # ── boundary validation ───────────────────────────────────────────────

    def _validate(self, params: dict) -> tuple[dict | None, str | None]:
        prompt = (params.get("prompt") or "").strip()
        if not prompt:
            return None, "Error: 'prompt' is required and cannot be empty."
        if len(prompt) > _MAX_PROMPT_CHARS:
            return None, (
                f"Error: prompt is {len(prompt)} characters — the maximum is "
                f"{_MAX_PROMPT_CHARS}. Shorten it."
            )

        aspect = (params.get("aspect_ratio") or _DEFAULT_ASPECT_RATIO).strip()
        if aspect not in _ALLOWED_ASPECT_RATIOS:
            return None, (
                f"Error: aspect_ratio '{aspect}' is not supported. "
                f"Choose one of: {', '.join(_ALLOWED_ASPECT_RATIOS)}."
            )

        n_raw = params.get("n", _MIN_N)
        try:
            n = int(n_raw)
        except (TypeError, ValueError):
            return None, f"Error: n must be an integer, got {n_raw!r}."
        if not (_MIN_N <= n <= _MAX_N):
            return None, f"Error: n must be between {_MIN_N} and {_MAX_N} (got {n})."

        return {"prompt": prompt, "aspect_ratio": aspect, "n": n}, None

    @staticmethod
    def _build_payload(clean: dict) -> dict:
        return {
            "model": _DEFAULT_MODEL,
            "prompt": clean["prompt"],
            "aspect_ratio": clean["aspect_ratio"],
            "n": clean["n"],
            "response_format": "url",
            "prompt_optimizer": True,
        }

    # ── execution ─────────────────────────────────────────────────────────

    async def execute(self, user_id: str, params: dict) -> str:
        import httpx

        clean, err = self._validate(params)
        if err:
            return err
        assert clean is not None  # for type-checkers

        api_key = (os.environ.get(_API_KEY_ENV) or "").strip()
        if not api_key:
            return (
                "Error: MiniMax image generation is not configured. Set the "
                f"{_API_KEY_ENV} environment variable (the same key the LLM "
                "router uses). Get one at https://platform.minimax.io."
            )

        payload = self._build_payload(clean)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                resp = await client.post(_API_URL, json=payload, headers=headers)
        except httpx.ConnectError as exc:
            logger.warning("MiniMax image unreachable: %s", exc)
            return "Image generation failed: cannot reach the MiniMax API."
        except httpx.TimeoutException:
            return f"Image generation timed out (>{int(_HTTP_TIMEOUT_S)}s)."
        except httpx.HTTPError as exc:
            logger.warning("MiniMax image request error: %s", exc)
            return f"Image generation failed: HTTP error talking to MiniMax ({exc})."

        if resp.status_code != 200:
            body = (resp.text or "")[:300]
            logger.warning("MiniMax image HTTP %d: %s", resp.status_code, body)
            if resp.status_code in (401, 403):
                return (
                    "Image generation failed: MiniMax rejected the API key "
                    f"(HTTP {resp.status_code}). Check {_API_KEY_ENV}."
                )
            return f"Image generation failed: MiniMax returned HTTP {resp.status_code}."

        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON 200 is a provider fault
            return "Image generation failed: MiniMax returned a non-JSON response."

        base_resp = data.get("base_resp") or {}
        status_code = base_resp.get("status_code")
        if status_code != 0:
            status_msg = base_resp.get("status_msg") or "unknown error"
            logger.warning("MiniMax image base_resp error %s: %s", status_code, status_msg)
            return f"Image generation failed: MiniMax error {status_code} — {status_msg}."

        urls = (data.get("data") or {}).get("image_urls") or []
        if not urls:
            return "Image generation failed: MiniMax returned no images."

        return await self._download_and_deliver(clean, urls)

    async def _download_and_deliver(self, clean: dict, urls: list[str]) -> str:
        """Download each generated image and deliver it via Telegram."""
        import httpx

        from lazyclaw.notifications.push import push_telegram_document

        snippet = clean["prompt"][:60] + ("…" if len(clean["prompt"]) > 60 else "")
        delivered = 0
        total_bytes = 0

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            for idx, url in enumerate(urls, start=1):
                try:
                    img_resp = await client.get(url)
                except httpx.HTTPError as exc:
                    logger.warning("Image %d download failed: %s", idx, exc)
                    continue
                if img_resp.status_code != 200:
                    logger.warning(
                        "Image %d download HTTP %d", idx, img_resp.status_code,
                    )
                    continue

                content = img_resp.content
                total_bytes += len(content)
                filename = f"image_{idx}.png" if len(urls) > 1 else "image.png"
                sent = await push_telegram_document(
                    self._config, content, filename, caption=f"🎨 {snippet}",
                )
                if sent:
                    delivered += 1

        if delivered == 0:
            return (
                f"Generated {len(urls)} image(s) for '{snippet}', but couldn't "
                "deliver them — configure Telegram (TELEGRAM_BOT_TOKEN + admin "
                "chat) to receive generated images."
            )
        return (
            f"Sent {delivered} image(s) ({total_bytes} bytes) to your Telegram "
            f"for '{snippet}'."
        )
