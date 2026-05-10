"""LazyClaw host-side STT bridge — a tiny FastAPI service that runs on the
macOS host so whisper.cpp can use Metal + Core ML ANE acceleration.

Why this exists
---------------
Docker Desktop on macOS runs Linux containers inside a Linux VM. Apple's
Metal and Core ML frameworks are macOS-host-only — the Linux VM cannot
access them, no matter what env vars or device passthroughs you try
(unlike NVIDIA on Linux hosts where GPU passthrough is well-supported).

So pywhispercpp inside the container falls back to CPU, which is 3-5x
slower than Metal on the same model. The fix is the same pattern the
project already uses for the host browser: run STT on the host, have the
container POST audio to it via ``host.docker.internal``.

Security model
--------------
The HTTP server binds to 0.0.0.0 because the container reaches the host
through ``host.docker.internal`` which resolves to a real LAN-shaped IP
(e.g. 192.168.65.254 on Docker Desktop), not 127.0.0.1. So we cannot bind
to localhost-only.

Mitigation: every request must carry ``Authorization: Bearer <token>``
matching ``LAZYCLAW_HOST_STT_TOKEN`` env. Same shape as host_bridge.py's
origin token. The token is generated per-install and stored in the repo's
.env file. On untrusted networks, the user should ``make host-stt-uninstall``.

This file is intentionally standalone — no ``lazyclaw.*`` imports — so the
host venv only needs four pip deps (fastapi, uvicorn, pywhispercpp,
imageio-ffmpeg) instead of the full lazyclaw dependency tree.

NOTE: do NOT add ``from __future__ import annotations`` to this module.
FastAPI's request-body resolver depends on resolving ``UploadFile`` / ``File``
type annotations at request time, and the future import turns them into
forward-ref strings that fail to resolve when the route handler is defined
inside ``_build_app()`` (not at module top level). Native ``str | None``
union syntax already works on the Python 3.11+ minimum we target, so we
don't need the future import here.
"""

import asyncio
import functools
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("lazyclaw.host-stt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_PORT = 18790
DEFAULT_MODEL = os.environ.get("LAZYCLAW_STT_MODEL", "small")

_model_lock = asyncio.Lock()
_model_instance = None


@functools.lru_cache(maxsize=1)
def _ffmpeg_path() -> str | None:
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).exists():
            return path
    except Exception as exc:  # noqa: BLE001
        logger.debug("imageio-ffmpeg unavailable: %s", exc)
    return shutil.which("ffmpeg")


async def _ensure_model():
    global _model_instance
    if _model_instance is not None:
        return _model_instance
    async with _model_lock:
        if _model_instance is not None:
            return _model_instance
        from pywhispercpp.model import Model

        loop = asyncio.get_running_loop()
        logger.info("Loading whisper.cpp model: %s", DEFAULT_MODEL)
        t0 = time.monotonic()
        _model_instance = await loop.run_in_executor(
            None,
            lambda: Model(
                DEFAULT_MODEL,
                print_realtime=False,
                print_progress=False,
            ),
        )
        logger.info("Model loaded in %.1fs", time.monotonic() - t0)
        return _model_instance


async def _decode_to_wav(src: Path) -> Path:
    ff = _ffmpeg_path()
    if ff is None:
        raise RuntimeError(
            "ffmpeg missing — reinstall the host-stt venv "
            "(scripts/install-host-stt-bridge.sh) so imageio-ffmpeg ships "
            "the bundled binary, or `brew install ffmpeg`.",
        )
    fd, dst = tempfile.mkstemp(suffix=".wav", prefix="lazyclaw_host_stt_")
    os.close(fd)
    proc = await asyncio.create_subprocess_exec(
        ff, "-y", "-i", str(src),
        "-ar", "16000", "-ac", "1", "-f", "wav", dst,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        Path(dst).unlink(missing_ok=True)
        snippet = (stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise RuntimeError(f"ffmpeg failed (exit={proc.returncode}): {snippet}")
    return Path(dst)


def _build_app():
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.responses import JSONResponse

    app = FastAPI(title="lazyclaw-host-stt", version="1.0")

    expected_token = os.environ.get("LAZYCLAW_HOST_STT_TOKEN", "").strip()
    if not expected_token:
        logger.error(
            "LAZYCLAW_HOST_STT_TOKEN is not set — refusing to start. "
            "Reinstall via scripts/install-host-stt-bridge.sh",
        )
        sys.exit(2)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        if header[7:] != expected_token:
            return JSONResponse({"error": "bad token"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {
            "ok": True,
            "model": DEFAULT_MODEL,
            "ffmpeg": _ffmpeg_path() or "missing",
            "loaded": _model_instance is not None,
        }

    @app.post("/transcribe")
    async def transcribe(
        audio: UploadFile = File(...),
        language: str | None = Form(default=None),
    ):
        # Persist uploaded bytes to a temp file so ffmpeg can read it.
        suffix = Path(audio.filename or "in.bin").suffix or ".bin"
        fd, in_path = tempfile.mkstemp(suffix=suffix, prefix="lazyclaw_host_stt_in_")
        os.close(fd)
        try:
            data = await audio.read()
            Path(in_path).write_bytes(data)

            wav_path = await _decode_to_wav(Path(in_path))
            try:
                model = await _ensure_model()
                loop = asyncio.get_running_loop()
                t0 = time.monotonic()

                def _run() -> list:
                    kwargs: dict = {}
                    if language:
                        kwargs["language"] = language
                    return list(model.transcribe(str(wav_path), **kwargs))

                segments = await loop.run_in_executor(None, _run)
                parts = [getattr(s, "text", "").strip() for s in segments]
                transcript = " ".join(p for p in parts if p).strip()
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "Transcribed %s bytes (%s) in %d ms → %d chars",
                    len(data), audio.filename or "?", duration_ms, len(transcript),
                )
                return {
                    "transcript": transcript,
                    "language": language,
                    "duration_ms": duration_ms,
                }
            finally:
                wav_path.unlink(missing_ok=True)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            Path(in_path).unlink(missing_ok=True)

    return app


def main() -> None:
    import uvicorn

    port = int(os.environ.get("LAZYCLAW_HOST_STT_PORT", DEFAULT_PORT))
    # Bind to 0.0.0.0 so host.docker.internal can reach us. Token is the gate.
    uvicorn.run(_build_app(), host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
