"""Audio I/O for LazyClaw.

Currently exposes speech-to-text via :mod:`lazyclaw.audio.stt`.
"""

from lazyclaw.audio.stt import STTUnavailableError, transcribe_file

__all__ = ["STTUnavailableError", "transcribe_file"]
