"""VaultWhisper configuration — mode, custom patterns, and AI provider keys."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VaultWhisperConfig:
    """Immutable configuration for VaultWhisper."""

    mode: str = "strict"  # "strict" or "relaxed"
    custom_patterns_json: str | None = None

    # AI provider keys (same as mcp-freeride, for proxy chat)
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    together_api_key: str | None = None
    mistral_api_key: str | None = None
    hf_api_key: str | None = None
    ollama_url: str = "http://localhost:11434"


def load_config() -> VaultWhisperConfig:
    """Load configuration from environment variables."""
    mode = os.getenv("VAULTWHISPER_MODE", "strict")
    if mode not in ("strict", "relaxed"):
        mode = "strict"

    return VaultWhisperConfig(
        mode=mode,
        custom_patterns_json=os.getenv("VAULTWHISPER_PATTERNS") or None,
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        together_api_key=os.getenv("TOGETHER_API_KEY") or None,
        mistral_api_key=os.getenv("MISTRAL_API_KEY") or None,
        hf_api_key=os.getenv("HF_API_KEY") or None,
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
    )


def get_configured_providers(config: VaultWhisperConfig) -> list[str]:
    """Return list of provider names that have API keys configured."""
    available: list[str] = []
    if config.groq_api_key:
        available.append("groq")
    if config.gemini_api_key:
        available.append("gemini")
    if config.openrouter_api_key:
        available.append("openrouter")
    if config.together_api_key:
        available.append("together")
    if config.mistral_api_key:
        available.append("mistral")
    if config.hf_api_key:
        available.append("huggingface")
    # Ollama is always available (local, no key needed)
    available.append("ollama")
    return available
