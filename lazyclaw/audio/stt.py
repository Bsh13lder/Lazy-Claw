"""Speech-to-text via whisper.cpp (pywhispercpp).

Lazy-loads a single model instance per process. Default is ``small`` to keep
RAM under ~1 GB on M-series Macs; override with the ``LAZYCLAW_STT_MODEL``
env var (``tiny`` / ``base`` / ``small`` / ``medium`` / ``large-v3``).

Apple Silicon: pywhispercpp uses Metal + Core ML when an ANE-encoded model
is present at the pywhispercpp model dir. CPU fallback otherwise.

Telegram voice notes arrive as OGG/Opus, so we shell out to ``ffmpeg`` to
decode to 16 kHz mono WAV before handing off to whisper.cpp.

ffmpeg resolution order:
1. ``imageio-ffmpeg`` bundled binary (installed automatically with the
   ``voice`` extra — no system install needed).
2. ``ffmpeg`` on ``$PATH`` (Homebrew / apt / etc).
3. Raise :class:`STTUnavailableError` with install instructions.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = os.environ.get("LAZYCLAW_STT_MODEL", "small")

_model_lock = asyncio.Lock()
_model_instance = None  # cached pywhispercpp.model.Model


class STTUnavailableError(RuntimeError):
    """Raised when STT cannot run (missing dep / ffmpeg / model)."""


@functools.lru_cache(maxsize=1)
def _ffmpeg_path() -> str | None:
    """Return absolute path to an ffmpeg binary, or None.

    Prefers the bundled binary from ``imageio-ffmpeg`` (auto-installed via the
    ``voice`` extra), then falls back to whatever ``ffmpeg`` is on PATH.
    """
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).exists():
            return path
    except Exception as exc:  # noqa: BLE001 — any failure → fall through
        logger.debug("imageio-ffmpeg unavailable: %s", exc)
    return shutil.which("ffmpeg")


def _has_ffmpeg() -> bool:
    return _ffmpeg_path() is not None


async def _ensure_model():
    """Lazy-instantiate the singleton whisper.cpp model.

    First call downloads + initialises the model (a few seconds for ``small``);
    subsequent calls are free. Concurrent callers serialise on a lock so we
    never load the model twice.
    """
    global _model_instance
    if _model_instance is not None:
        return _model_instance

    async with _model_lock:
        if _model_instance is not None:
            return _model_instance
        try:
            from pywhispercpp.model import Model
        except ImportError as exc:
            raise STTUnavailableError(
                "pywhispercpp not importable — usually means the wheel failed "
                "to build at install time. pywhispercpp is a base dependency "
                "of lazyclaw, so reinstall: "
                "`pipx reinstall lazyclaw` (or `pip install --force-reinstall "
                "pywhispercpp` inside your venv). On platforms without "
                "prebuilt wheels you may need cmake + a C++ toolchain.",
            ) from exc

        loop = asyncio.get_running_loop()
        logger.info("Loading whisper.cpp model: %s", _DEFAULT_MODEL)
        _model_instance = await loop.run_in_executor(
            None,
            lambda: Model(
                _DEFAULT_MODEL,
                print_realtime=False,
                print_progress=False,
            ),
        )
        return _model_instance


async def _decode_to_wav(src: Path) -> Path:
    """Convert any audio file to 16 kHz mono WAV via ffmpeg.

    Returns the path to a freshly-created temp WAV. The caller owns cleanup.
    """
    ff = _ffmpeg_path()
    if ff is None:
        raise STTUnavailableError(
            "ffmpeg missing. imageio-ffmpeg is a base dep of lazyclaw and "
            "ships a bundled binary, so this usually means a broken venv. "
            "Reinstall: `pipx reinstall lazyclaw` (or `pip install "
            "--force-reinstall imageio-ffmpeg`). Alternatively install "
            "system ffmpeg (`brew install ffmpeg` on macOS, "
            "`apt install ffmpeg` on Linux).",
        )
    fd, dst = tempfile.mkstemp(suffix=".wav", prefix="lazyclaw_stt_")
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


async def transcribe_file(
    path: str | Path,
    *,
    language: str | None = None,
) -> str:
    """Transcribe an audio file and return the joined transcript.

    Accepts ogg / opus / m4a / mp3 / wav — anything ffmpeg decodes.
    *language* is an optional ISO-639-1 code (``"en"``, ``"es"``, ``"ka"``);
    leave ``None`` to let whisper auto-detect.

    Returns the concatenated, whitespace-trimmed transcript. Raises
    :class:`STTUnavailableError` when dependencies are missing.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    wav_path = await _decode_to_wav(path)
    try:
        model = await _ensure_model()
        loop = asyncio.get_running_loop()

        def _run() -> list:
            kwargs: dict = {}
            if language:
                kwargs["language"] = language
            return list(model.transcribe(str(wav_path), **kwargs))

        segments = await loop.run_in_executor(None, _run)
        parts = [getattr(s, "text", "").strip() for s in segments]
        return " ".join(p for p in parts if p).strip()
    finally:
        wav_path.unlink(missing_ok=True)
