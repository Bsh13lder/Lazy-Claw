from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskAIConfig:
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    together_api_key: str | None = None
    mistral_api_key: str | None = None
    hf_api_key: str | None = None
    ollama_url: str = "http://localhost:11434"
    preferred_provider: str | None = None
    max_tokens: int = 1024


def load_config() -> TaskAIConfig:
    return TaskAIConfig(
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        together_api_key=os.getenv("TOGETHER_API_KEY") or None,
        mistral_api_key=os.getenv("MISTRAL_API_KEY") or None,
        hf_api_key=os.getenv("HF_API_KEY") or None,
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        preferred_provider=os.getenv("TASKAI_PROVIDER") or None,
        max_tokens=int(os.getenv("TASKAI_MAX_TOKENS", "1024")),
    )
