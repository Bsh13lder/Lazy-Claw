from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TaskAIConfig:
    provider_names: list[str] = field(default_factory=lambda: ["groq", "gemini"])
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None


def load_config() -> TaskAIConfig:
    providers: list[str] = []
    groq_key = os.environ.get("GROQ_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if groq_key:
        providers.append("groq")
    if gemini_key:
        providers.append("gemini")
    if openrouter_key:
        providers.append("openrouter")
    return TaskAIConfig(
        provider_names=providers or ["none"],
        groq_api_key=groq_key,
        gemini_api_key=gemini_key,
        openrouter_api_key=openrouter_key,
    )
