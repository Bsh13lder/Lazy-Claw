from __future__ import annotations
import os
from dataclasses import dataclass, field

@dataclass
class FreeRideConfig:
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    together_api_key: str | None = None
    mistral_api_key: str | None = None
    hf_api_key: str | None = None
    ollama_url: str = "http://localhost:11434"
    preferred_order: list[str] = field(default_factory=lambda: [
        "groq", "gemini", "openrouter", "together", "mistral", "huggingface", "ollama",
    ])

def load_config() -> FreeRideConfig:
    return FreeRideConfig(
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        together_api_key=os.getenv("TOGETHER_API_KEY") or None,
        mistral_api_key=os.getenv("MISTRAL_API_KEY") or None,
        hf_api_key=os.getenv("HF_API_KEY") or None,
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
    )

def get_configured_providers(config: FreeRideConfig) -> list[str]:
    """Return list of provider names that have API keys configured."""
    available = []
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
    # Ollama only if explicitly configured via env var (not the default URL)
    if os.getenv("OLLAMA_URL"):
        available.append("ollama")
    return available
