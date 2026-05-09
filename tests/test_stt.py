"""Smoke tests for :mod:`lazyclaw.audio.stt`.

Real transcription needs ffmpeg + the whisper.cpp model, so we don't load the
model in CI. These tests cover the contract: missing dep raises a clear error,
ffmpeg detection works, the public surface stays stable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lazyclaw.audio import stt


def test_public_surface() -> None:
    assert callable(stt.transcribe_file)
    assert issubclass(stt.STTUnavailableError, RuntimeError)


@pytest.mark.asyncio
async def test_missing_pywhispercpp_raises_unavailable(monkeypatch) -> None:
    """When pywhispercpp is absent, _ensure_model surfaces a friendly error."""
    # Pretend the import always fails
    monkeypatch.setitem(sys.modules, "pywhispercpp", None)
    monkeypatch.setitem(sys.modules, "pywhispercpp.model", None)
    # Reset the singleton so the lazy loader actually runs.
    monkeypatch.setattr(stt, "_model_instance", None, raising=False)

    with pytest.raises(stt.STTUnavailableError, match="pywhispercpp"):
        await stt._ensure_model()


@pytest.mark.asyncio
async def test_missing_ffmpeg_raises_unavailable(monkeypatch, tmp_path) -> None:
    """_decode_to_wav fails fast when neither bundled nor system ffmpeg is found."""
    stt._ffmpeg_path.cache_clear()
    monkeypatch.setattr(stt, "_ffmpeg_path", lambda: None)
    fake = tmp_path / "x.ogg"
    fake.write_bytes(b"")
    with pytest.raises(stt.STTUnavailableError, match="ffmpeg"):
        await stt._decode_to_wav(fake)


def test_ffmpeg_path_prefers_imageio(monkeypatch, tmp_path) -> None:
    """imageio-ffmpeg's bundled binary wins over system PATH when present."""
    stt._ffmpeg_path.cache_clear()
    fake = tmp_path / "ffmpeg-bundled"
    fake.write_text("")  # exists check only

    import sys
    import types

    fake_mod = types.ModuleType("imageio_ffmpeg")
    fake_mod.get_ffmpeg_exe = lambda: str(fake)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake_mod)

    assert stt._ffmpeg_path() == str(fake)
    stt._ffmpeg_path.cache_clear()


@pytest.mark.asyncio
async def test_transcribe_file_missing_path() -> None:
    with pytest.raises(FileNotFoundError):
        await stt.transcribe_file(Path("/nonexistent/audio.ogg"))
