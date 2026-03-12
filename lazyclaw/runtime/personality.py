from __future__ import annotations

from pathlib import Path

_FALLBACK_PERSONALITY = (
    "You are Claw, a helpful AI assistant. Be direct, friendly, and efficient."
)


def _find_project_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


def load_personality(personality_path: str | None = None) -> str:
    root = _find_project_root()
    path = root / (personality_path or "personality/SOUL.md")
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _FALLBACK_PERSONALITY


def build_system_prompt(
    personality: str, extra_context: str | None = None
) -> str:
    if extra_context:
        return f"{personality}\n\n---\n\n{extra_context}"
    return personality
