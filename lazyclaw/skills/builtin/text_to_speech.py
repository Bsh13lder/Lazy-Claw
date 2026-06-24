"""Text-to-speech (TTS) via MiniMax's T2A v2 API.

The mirror image of ``lazyclaw/audio/stt.py`` (speech-to-TEXT): this skill
takes text and produces an audio file, then delivers it to the user the same
way the ``send_*`` file-producing skills do — a Telegram document attachment
when Telegram is configured, with a web-download link always returned as a
fallback.

API (official MiniMax intl host, June 2026):
    POST https://api.minimax.io/v1/t2a_v2
    Authorization: Bearer <MINIMAX_API_KEY>
    Content-Type: application/json

The key comes from the ``MINIMAX_API_KEY`` env var — the same env var the LLM
router already uses (``llm/router.py``), so no new secret config is introduced.
There is NO GroupId on the current intl host (it lives in the URL on the
legacy/China host only).

Request body shape — note ``text`` is TOP-LEVEL, not wrapped::

    {
      "model": "speech-2.8-hd",
      "text": "<str>",
      "stream": false,
      "language_boost": "auto",
      "output_format": "url",
      "voice_setting": {"voice_id": "...", "speed": 1, "vol": 1, "pitch": 0},
      "audio_setting": {"sample_rate": 32000, "bitrate": 128000,
                        "format": "mp3", "channel": 1}
    }

``output_format`` may be ``"url"`` (a download URL, valid ~9-24h) or ``"hex"``
(raw audio as a hex string — decode with ``bytes.fromhex``). We request
``"url"`` and download the bytes, but tolerate a ``hex`` payload too so a
server-side default swap can't break delivery.

ALWAYS check ``base_resp.status_code == 0`` — a 200 HTTP status with a non-zero
``base_resp.status_code`` is still a failure (e.g. auth / quota / bad voice).

Long text: an async endpoint (``/v1/t2a_async_v2``, up to ~1M chars) exists for
batch synthesis. We keep the synchronous path here and cap the input length
(see :data:`_MAX_TEXT_CHARS`); routing very long text through the async job API
is a follow-up.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_API_URL = "https://api.minimax.io/v1/t2a_v2"
_API_KEY_ENV = "MINIMAX_API_KEY"

_DEFAULT_MODEL = "speech-2.8-hd"
_ALLOWED_MODELS = ("speech-2.8-hd", "speech-2.8-turbo")
_DEFAULT_VOICE_ID = "English_expressive_narrator"
_DEFAULT_FORMAT = "mp3"
_ALLOWED_FORMATS = ("mp3", "wav", "pcm", "flac")

_MIN_SPEED = 0.5
_MAX_SPEED = 2.0
_DEFAULT_SPEED = 1.0

# Cap synchronous input. MiniMax's sync T2A accepts well above this, but very
# long text belongs on the async job API (``/v1/t2a_async_v2``); we fail fast
# with a clear message instead of timing out a 5-minute synthesis on the sync
# path. Raise the cap (or route to the async endpoint) as a follow-up.
_MAX_TEXT_CHARS = 8000

_HTTP_TIMEOUT_S = 120.0


class TextToSpeechSkill(BaseSkill):
    """Synthesize speech from text via MiniMax and deliver the audio file."""

    def __init__(self, config: Any = None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "text_to_speech"

    @property
    def display_name(self) -> str:
        return "Text to Speech"

    @property
    def description(self) -> str:
        return (
            "Convert text into spoken audio via MiniMax TTS and send it to the "
            "user (e.g. 'read this out loud', 'turn this into a voice note', "
            "'narrate this paragraph'). Delivers an mp3 as a Telegram document "
            "when Telegram is configured; always returns a web download link "
            "too. Pick a voice_id, speaking speed (0.5-2), model, and format."
        )

    @property
    def category(self) -> str:
        # `utility` is ALLOW by default (permissions/models.py). Synthesis runs
        # against the user's own MiniMax key and only produces a local audio
        # file delivered back to them — no external blast radius — so it lives
        # alongside calculate/get_time rather than a gated category.
        return "utility"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": (
                        f"Text to synthesize (required, up to {_MAX_TEXT_CHARS} "
                        "characters)."
                    ),
                },
                "voice_id": {
                    "type": "string",
                    "description": (
                        "MiniMax voice id (string). Default "
                        f"'{_DEFAULT_VOICE_ID}'. Examples: "
                        "'English_expressive_narrator', "
                        "'English_CalmWoman', 'English_Gentleman'."
                    ),
                },
                "speed": {
                    "type": "number",
                    "description": (
                        f"Speaking speed, {_MIN_SPEED}-{_MAX_SPEED} "
                        f"(default {_DEFAULT_SPEED}; 1 = natural pace)."
                    ),
                },
                "model": {
                    "type": "string",
                    "enum": list(_ALLOWED_MODELS),
                    "description": (
                        f"TTS model (default '{_DEFAULT_MODEL}'). "
                        "'speech-2.8-turbo' is faster/cheaper."
                    ),
                },
                "format": {
                    "type": "string",
                    "enum": list(_ALLOWED_FORMATS),
                    "description": f"Audio container (default '{_DEFAULT_FORMAT}').",
                },
            },
            "required": ["text"],
        }

    # ── boundary validation ───────────────────────────────────────────────

    def _validate(self, params: dict) -> tuple[dict | None, str | None]:
        """Validate + normalize params. Returns ``(clean, None)`` or
        ``(None, error_message)``.
        """
        text = (params.get("text") or "").strip()
        if not text:
            return None, "Error: 'text' is required and cannot be empty."
        if len(text) > _MAX_TEXT_CHARS:
            return None, (
                f"Error: text is {len(text)} characters — the synchronous TTS "
                f"path caps at {_MAX_TEXT_CHARS}. Split it into smaller chunks "
                "(a longer-text async endpoint is a planned follow-up)."
            )

        voice_id = (params.get("voice_id") or _DEFAULT_VOICE_ID).strip()
        if not voice_id:
            voice_id = _DEFAULT_VOICE_ID

        model = (params.get("model") or _DEFAULT_MODEL).strip()
        if model not in _ALLOWED_MODELS:
            return None, (
                f"Error: model '{model}' is not supported. "
                f"Choose one of: {', '.join(_ALLOWED_MODELS)}."
            )

        fmt = (params.get("format") or _DEFAULT_FORMAT).strip().lower()
        if fmt not in _ALLOWED_FORMATS:
            return None, (
                f"Error: format '{fmt}' is not supported. "
                f"Choose one of: {', '.join(_ALLOWED_FORMATS)}."
            )

        speed_raw = params.get("speed", _DEFAULT_SPEED)
        try:
            speed = float(speed_raw)
        except (TypeError, ValueError):
            return None, f"Error: speed must be a number, got {speed_raw!r}."
        if not (_MIN_SPEED <= speed <= _MAX_SPEED):
            return None, (
                f"Error: speed must be between {_MIN_SPEED} and {_MAX_SPEED} "
                f"(got {speed})."
            )

        return {
            "text": text,
            "voice_id": voice_id,
            "model": model,
            "format": fmt,
            "speed": speed,
        }, None

    @staticmethod
    def _build_payload(clean: dict) -> dict:
        return {
            "model": clean["model"],
            "text": clean["text"],
            "stream": False,
            "language_boost": "auto",
            "output_format": "url",
            "voice_setting": {
                "voice_id": clean["voice_id"],
                "speed": clean["speed"],
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": clean["format"],
                "channel": 1,
            },
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
                "Error: MiniMax TTS is not configured. Set the "
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
            logger.warning("MiniMax TTS unreachable: %s", exc)
            return "TTS failed: cannot reach the MiniMax API. Check your network."
        except httpx.TimeoutException:
            return (
                f"TTS timed out (>{int(_HTTP_TIMEOUT_S)}s). Try shorter text "
                "or the 'speech-2.8-turbo' model."
            )
        except httpx.HTTPError as exc:  # any other transport-level error
            logger.warning("MiniMax TTS request error: %s", exc)
            return f"TTS failed: HTTP error talking to MiniMax ({exc})."

        if resp.status_code != 200:
            body = (resp.text or "")[:300]
            logger.warning(
                "MiniMax TTS HTTP %d: %s", resp.status_code, body,
            )
            if resp.status_code in (401, 403):
                return (
                    "TTS failed: MiniMax rejected the API key (HTTP "
                    f"{resp.status_code}). Check {_API_KEY_ENV}."
                )
            return f"TTS failed: MiniMax returned HTTP {resp.status_code}."

        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON 200 is a provider fault
            return "TTS failed: MiniMax returned a non-JSON response."

        # base_resp.status_code == 0 means success; anything else is an error
        # even though the HTTP status was 200 (auth/quota/bad-voice/etc).
        base_resp = data.get("base_resp") or {}
        status_code = base_resp.get("status_code")
        if status_code != 0:
            status_msg = base_resp.get("status_msg") or "unknown error"
            logger.warning(
                "MiniMax TTS base_resp error %s: %s", status_code, status_msg,
            )
            return (
                f"TTS failed: MiniMax error {status_code} — {status_msg}."
            )

        audio, fetch_err = await self._extract_audio_bytes(data)
        if fetch_err:
            return fetch_err
        if not audio:
            return "TTS failed: MiniMax returned no audio data."

        return await self._deliver(clean, audio)

    async def _extract_audio_bytes(
        self, data: dict,
    ) -> tuple[bytes | None, str | None]:
        """Pull the audio bytes out of a successful response.

        Prefers the ``data.audio`` URL (``output_format='url'``) and downloads
        it; falls back to decoding a hex string (``output_format='hex'``) so a
        server-side default change can't break delivery. Returns
        ``(bytes, None)`` or ``(None, error_message)``.
        """
        import httpx

        audio_field = (data.get("data") or {}).get("audio")
        if not audio_field or not isinstance(audio_field, str):
            return None, "TTS failed: response had no 'data.audio' field."

        # A URL payload starts with http(s); a hex payload is hex digits only.
        if audio_field.startswith(("http://", "https://")):
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                    audio_resp = await client.get(audio_field)
            except httpx.HTTPError as exc:
                logger.warning("MiniMax TTS audio download failed: %s", exc)
                return None, f"TTS failed: could not download audio ({exc})."
            if audio_resp.status_code != 200:
                return None, (
                    "TTS failed: audio download returned HTTP "
                    f"{audio_resp.status_code}."
                )
            return audio_resp.content, None

        # Otherwise treat it as a raw hex string.
        try:
            return bytes.fromhex(audio_field), None
        except ValueError:
            return None, (
                "TTS failed: audio payload was neither a URL nor valid hex."
            )

    async def _deliver(self, clean: dict, audio: bytes) -> str:
        """Deliver the audio file via Telegram, with a web-download fallback.

        Mirrors ``send_sheet``: push the bytes as a Telegram document when
        configured, and always surface a web download link.
        """
        from lazyclaw.notifications.push import push_telegram_document

        fmt = clean["format"]
        filename = f"speech.{fmt}"
        size = len(audio)
        snippet = clean["text"][:60] + ("…" if len(clean["text"]) > 60 else "")

        sent = await push_telegram_document(
            self._config,
            audio,
            filename,
            caption=f"🔊 {snippet}",
        )
        if sent:
            return (
                f"Sent **{filename}** ({size} bytes) to your Telegram — "
                f"narrated with voice '{clean['voice_id']}'."
            )
        return (
            f"Synthesized **{filename}** ({size} bytes) with voice "
            f"'{clean['voice_id']}', but Telegram isn't configured to deliver "
            "it. Configure Telegram (TELEGRAM_BOT_TOKEN + admin chat) to "
            "receive generated audio."
        )
