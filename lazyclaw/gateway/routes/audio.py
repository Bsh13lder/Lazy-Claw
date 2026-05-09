"""Audio routes — speech-to-text for Web UI voice input.

Mirrors the Telegram voice flow: client uploads an audio blob, we transcribe
via whisper.cpp, return the text. The client then puts the transcript in the
chat input so the user can edit before sending.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from lazyclaw.audio.stt import STTUnavailableError, transcribe_file
from lazyclaw.gateway.auth import User, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audio", tags=["audio"])

# Generous cap. Telegram voice notes top out around 5 MB; browser MediaRecorder
# clips during a chat session shouldn't approach this.
_MAX_BYTES = 25 * 1024 * 1024


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Transcribe an uploaded audio file.

    Accepts anything ffmpeg understands: webm/opus (browser MediaRecorder
    default), ogg/opus, mp4/m4a, wav, mp3, etc. Returns ``{"text": ...}``.
    """
    # Pick a sensible suffix so ffmpeg can sniff format from the extension if
    # the magic bytes are ambiguous.
    suffix = Path(file.filename or "audio").suffix or ".webm"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="lazyclaw_upload_")
    os.close(fd)
    tmp = Path(tmp_path)
    bytes_written = 0
    try:
        with tmp.open("wb") as fh:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > _MAX_BYTES:
                    raise HTTPException(413, "Audio file exceeds 25 MB cap")
                fh.write(chunk)
        if bytes_written == 0:
            raise HTTPException(400, "Empty upload")

        try:
            text = await transcribe_file(tmp)
        except STTUnavailableError as exc:
            # 503 — server is missing pywhispercpp or ffmpeg, surface clearly.
            raise HTTPException(503, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(500, f"Audio file vanished: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — surface unknowns to client
            logger.exception("Transcription failed for user=%s", user.id)
            raise HTTPException(500, f"Transcription failed: {exc}") from exc

        return {"text": text}
    finally:
        tmp.unlink(missing_ok=True)
