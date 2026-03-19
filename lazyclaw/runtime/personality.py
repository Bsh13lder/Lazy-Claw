"""Personality loader with filesystem caching.

SOUL.md is read once and cached in memory. Re-reads only when
the file's mtime changes (e.g., user edits the file).
"""

from __future__ import annotations

from pathlib import Path

_FALLBACK_PERSONALITY = (
    "You are Claw, a helpful AI assistant. Be direct, friendly, and efficient."
)

# In-memory cache: content + mtime
_cache: str | None = None
_cache_mtime: float = 0.0


def _find_project_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


def load_personality(personality_path: str | None = None) -> str:
    """Load SOUL.md with mtime-based caching (~0ms on cache hit)."""
    global _cache, _cache_mtime

    root = _find_project_root()
    path = root / (personality_path or "personality/SOUL.md")
    try:
        mtime = path.stat().st_mtime
        if _cache is not None and mtime == _cache_mtime:
            return _cache
        _cache = path.read_text(encoding="utf-8")
        _cache_mtime = mtime
        return _cache
    except FileNotFoundError:
        return _FALLBACK_PERSONALITY


def build_system_prompt(
    personality: str, extra_context: str | None = None
) -> str:
    if extra_context:
        return f"{personality}\n\n---\n\n{extra_context}"
    return personality
