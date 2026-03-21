"""Static model catalog and routing result for ECO local mode.

Defines ModelProfile for all known models (local + paid) and RoutingResult
for tracking which model handled each request. Used by eco_router for
routing decisions and by summary/TUI for attribution display.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    """Immutable profile for a known AI model."""

    name: str               # Ollama model name or API model ID
    display_name: str       # Shown in TUI/Telegram
    provider: str           # "ollama" | "openai" | "anthropic" | "mcp"
    is_local: bool
    ram_mb: int             # Estimated RAM when loaded (0 for remote)
    cost_input: float       # USD per 1K input tokens (0.0 for local)
    cost_output: float      # USD per 1K output tokens (0.0 for local)
    icon: str               # Emoji for TUI display
    max_context: int        # Max context window tokens
    tool_calling: bool      # Supports function/tool calling
    role: str               # "brain" | "specialist" | "coder" | "fallback"


# ── Model names (constants for imports) ───────────────────────────────

BRAIN_MODEL = "qwen3:0.6b"
SPECIALIST_MODEL = "qwen3:0.6b"


# ── Model catalog ─────────────────────────────────────────────────────

MODEL_CATALOG: dict[str, ModelProfile] = {
    # LOCAL — FREE (Ollama)
    "qwen3:0.6b": ModelProfile(
        name="qwen3:0.6b",
        display_name="qwen3:0.6b",
        provider="ollama",
        is_local=True,
        ram_mb=500,
        cost_input=0.0,
        cost_output=0.0,
        icon="\U0001f9e0",  # 🧠
        max_context=32768,
        tool_calling=True,
        role="brain",
    ),
    "qwen3:1.7b": ModelProfile(
        name="qwen3:1.7b",
        display_name="qwen3:1.7b",
        provider="ollama",
        is_local=True,
        ram_mb=1100,
        cost_input=0.0,
        cost_output=0.0,
        icon="\U0001f9e0",  # 🧠
        max_context=32768,
        tool_calling=True,
        role="brain",
    ),
    "softw8/nanbeige4.1-3b-tools": ModelProfile(
        name="softw8/nanbeige4.1-3b-tools",
        display_name="nanbeige4.1-3b",
        provider="ollama",
        is_local=True,
        ram_mb=2500,
        cost_input=0.0,
        cost_output=0.0,
        icon="\U0001f916",  # 🤖
        max_context=262144,
        tool_calling=True,
        role="specialist",
    ),
    # MCP — FREE (user's Claude subscription)
    "claude_code": ModelProfile(
        name="claude_code",
        display_name="claude_code",
        provider="mcp",
        is_local=False,
        ram_mb=0,
        cost_input=0.0,
        cost_output=0.0,
        icon="\u26a1",  # ⚡
        max_context=200000,
        tool_calling=True,
        role="coder",
    ),
    # PAID — fallback only
    "gpt-5-mini": ModelProfile(
        name="gpt-5-mini",
        display_name="gpt-5-mini",
        provider="openai",
        is_local=False,
        ram_mb=0,
        cost_input=0.00015,
        cost_output=0.0006,
        icon="\U0001f4b0",  # 💰
        max_context=128000,
        tool_calling=True,
        role="fallback",
    ),
    "gpt-5": ModelProfile(
        name="gpt-5",
        display_name="gpt-5",
        provider="openai",
        is_local=False,
        ram_mb=0,
        cost_input=0.005,
        cost_output=0.015,
        icon="\U0001f4b0",  # 💰
        max_context=128000,
        tool_calling=True,
        role="fallback",
    ),
}


# ── Routing result ────────────────────────────────────────────────────

@dataclass(frozen=True)
class RoutingResult:
    """Immutable record of which model handled a request and why."""

    model: str          # e.g. "qwen3:0.6b"
    provider: str       # e.g. "ollama"
    is_local: bool
    reason: str         # e.g. "simple_chat -> brain"

    @property
    def display_name(self) -> str:
        profile = get_model(self.model)
        return profile.display_name if profile else self.model

    @property
    def icon(self) -> str:
        profile = get_model(self.model)
        return profile.icon if profile else "\U0001f916"

    @property
    def cost_label(self) -> str:
        return "FREE" if self.is_local else "PAID"


# ── Helpers ───────────────────────────────────────────────────────────

def get_model(name: str) -> ModelProfile | None:
    """Look up a model profile by name. Returns None if unknown."""
    return MODEL_CATALOG.get(name)


def get_local_models() -> tuple[ModelProfile, ...]:
    """Return all local (Ollama) model profiles."""
    return tuple(m for m in MODEL_CATALOG.values() if m.is_local)


def total_local_ram_mb() -> int:
    """Total estimated RAM for all local models."""
    return sum(m.ram_mb for m in MODEL_CATALOG.values() if m.is_local)
