"""Static model catalog and role-based mode table for ECO routing.

Three roles: Brain (= Team Lead), Worker, Fallback.
Three modes:
  HYBRID:  Sonnet brain + Gemma 4 E2B local worker ($0) + Haiku fallback (auto)
  FULL:    User-configurable brain/worker/fallback (paid, auto)
  CLAUDE:  All roles via claude CLI ($0 — covered by subscription)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    """Immutable profile for a known AI model."""

    name: str               # Model ID (Ollama name, MLX HF path, or API model ID)
    display_name: str       # Shown in TUI/Telegram
    provider: str           # "mlx" | "ollama" | "openai" | "anthropic" | "mcp"
    is_local: bool
    ram_mb: int             # Estimated RAM when loaded (0 for remote)
    cost_input: float       # USD per 1M input tokens (0.0 for local)
    cost_output: float      # USD per 1M output tokens (0.0 for local)
    icon: str               # Emoji for TUI display
    max_context: int        # Max context window tokens
    tool_calling: bool      # Supports function/tool calling
    role: str               # "brain" | "worker" | "coder" | "fallback"


# ── Mode → Model table (single source of truth) ─────────────────────

MODE_MODELS: dict[str, dict[str, str]] = {
    "hybrid": {
        # Sonnet 4.6 brain (paid) + LazyClaw Gemma 4 E2B via Ollama (free local worker)
        "brain":    "claude-sonnet-4-6",
        "worker":   "lazyclaw-e2b",
        "fallback": "claude-haiku-4-5-20251001",
    },
    "full": {
        # All-Claude paid tier: Sonnet brain + Haiku workers
        "brain":    "claude-sonnet-4-6",
        "worker":   "claude-haiku-4-5-20251001",
        "fallback": "claude-sonnet-4-6",
    },
    "claude": {
        # Haiku brain (cheap API, native tool_use) + CLI fallback ($0)
        "brain":    "claude-haiku-4-5-20251001",
        "worker":   "claude-haiku-4-5-20251001",
        "fallback": "claude-cli",
    },
}

# Ollama local models (custom Modelfiles with agent identity baked in)
OLLAMA_WORKER_MODEL = "lazyclaw-e2b"


def get_mode_models(mode: str) -> dict[str, str]:
    """Get brain/worker/fallback model IDs for a mode."""
    return dict(MODE_MODELS.get(mode, MODE_MODELS["hybrid"]))


# ── Backward-compat aliases (used by imports, remove later) ──────────

BRAIN_MODEL = MODE_MODELS["hybrid"]["brain"]     # claude-sonnet-4-6
WORKER_MODEL = MODE_MODELS["hybrid"]["worker"]   # gemma4:e2b (Ollama)
FALLBACK_MODEL = MODE_MODELS["hybrid"]["fallback"]
PAID_BRAIN_MODEL = MODE_MODELS["full"]["brain"]
PAID_WORKER_MODEL = MODE_MODELS["full"]["worker"]


# ── Model catalog ─────────────────────────────────────────────────────

MODEL_CATALOG: dict[str, ModelProfile] = {
    # ── LOCAL — Ollama (Gemma 4 with LazyClaw agent identity baked in) ──
    # Custom Modelfiles: agent SYSTEM prompt + low temperature + 32K ctx
    # Default worker: E2B — fits 16GB M2, native tool calling
    "lazyclaw-e2b": ModelProfile(
        name="lazyclaw-e2b",
        display_name="Gemma 4 E2B",
        provider="ollama",
        is_local=True,
        ram_mb=7200,
        cost_input=0.0,
        cost_output=0.0,
        icon="\U0001f916",  # 🤖
        max_context=32768,
        tool_calling=True,
        role="worker",
    ),
    # E4B — better quality brain, tighter on 16GB (~9.6GB)
    "lazyclaw-e4b": ModelProfile(
        name="lazyclaw-e4b",
        display_name="Gemma 4 E4B",
        provider="ollama",
        is_local=True,
        ram_mb=9600,
        cost_input=0.0,
        cost_output=0.0,
        icon="\U0001f9e0",  # 🧠
        max_context=32768,
        tool_calling=True,
        role="brain",
    ),
    # Base models (without agent identity — for reference/fallback)
    "gemma4:e2b": ModelProfile(
        name="gemma4:e2b",
        display_name="Gemma 4 E2B (base)",
        provider="ollama",
        is_local=True,
        ram_mb=7200,
        cost_input=0.0,
        cost_output=0.0,
        icon="\U0001f916",  # 🤖
        max_context=131072,
        tool_calling=True,
        role="worker",
    ),
    "gemma4:e4b": ModelProfile(
        name="gemma4:e4b",
        display_name="Gemma 4 E4B (base)",
        provider="ollama",
        is_local=True,
        ram_mb=9600,
        cost_input=0.0,
        cost_output=0.0,
        icon="\U0001f9e0",  # 🧠
        max_context=131072,
        tool_calling=True,
        role="brain",
    ),

    # ── CLI — FREE (user's Claude subscription) ────────────────────────
    "claude-cli": ModelProfile(
        name="claude-cli",
        display_name="Claude CLI",
        provider="claude_cli",
        is_local=False,
        ram_mb=0,
        cost_input=0.0,
        cost_output=0.0,
        icon="\u26a1",  # ⚡
        max_context=200000,
        tool_calling=True,
        role="brain",
    ),

    # ── MCP — FREE (user's Claude subscription) ──────────────────────
    "claude_code": ModelProfile(
        name="claude_code",
        display_name="Claude Code",
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

    # ── PAID — Claude (fallback + ECO OFF) ────────────────────────────
    "claude-sonnet-4-6": ModelProfile(
        name="claude-sonnet-4-6",
        display_name="Sonnet 4.6",
        provider="anthropic",
        is_local=False,
        ram_mb=0,
        cost_input=3.0,      # $3/M input
        cost_output=15.0,     # $15/M output
        icon="\U0001f4ab",    # 💫
        max_context=200000,
        tool_calling=True,
        role="brain",
    ),
    "claude-haiku-4-5-20251001": ModelProfile(
        name="claude-haiku-4-5-20251001",
        display_name="Haiku 4.5",
        provider="anthropic",
        is_local=False,
        ram_mb=0,
        cost_input=1.0,       # $1/M input
        cost_output=5.0,       # $5/M output
        icon="\U0001f343",    # 🍃
        max_context=200000,
        tool_calling=True,
        role="worker",
    ),

    "claude-opus-4-6": ModelProfile(
        name="claude-opus-4-6",
        display_name="Opus 4.6",
        provider="anthropic",
        is_local=False,
        ram_mb=0,
        cost_input=15.0,      # $15/M input
        cost_output=75.0,     # $75/M output
        icon="\U0001f48e",    # 💎
        max_context=200000,
        tool_calling=True,
        role="fallback",
    ),

    # ── PAID — OpenAI (legacy, kept for users with OpenAI keys) ───────
    "gpt-5-mini": ModelProfile(
        name="gpt-5-mini",
        display_name="GPT-5 Mini",
        provider="openai",
        is_local=False,
        ram_mb=0,
        cost_input=0.15,
        cost_output=0.6,
        icon="\U0001f4b0",  # 💰
        max_context=128000,
        tool_calling=True,
        role="fallback",
    ),
    "gpt-5": ModelProfile(
        name="gpt-5",
        display_name="GPT-5",
        provider="openai",
        is_local=False,
        ram_mb=0,
        cost_input=5.0,
        cost_output=15.0,
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

    model: str          # e.g. "gemma4:e2b"
    provider: str       # e.g. "mlx"
    is_local: bool
    reason: str         # e.g. "eco_on: brain -> chat"

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
        if self.is_local:
            return "FREE"
        profile = get_model(self.model)
        if profile and profile.cost_input == 0.0:
            return "FREE"
        return "PAID"


# ── Helpers ───────────────────────────────────────────────────────────

def get_model(name: str) -> ModelProfile | None:
    """Look up a model profile by name. Returns None if unknown."""
    return MODEL_CATALOG.get(name)


def get_local_models() -> tuple[ModelProfile, ...]:
    """Return all local model profiles (MLX + Ollama)."""
    return tuple(m for m in MODEL_CATALOG.values() if m.is_local)


def get_mlx_models() -> tuple[ModelProfile, ...]:
    """Return MLX-specific model profiles."""
    return tuple(m for m in MODEL_CATALOG.values() if m.provider == "mlx")


def get_paid_models() -> tuple[ModelProfile, ...]:
    """Return all paid model profiles."""
    return tuple(
        m for m in MODEL_CATALOG.values()
        if not m.is_local and m.cost_input > 0
    )


def total_local_ram_mb() -> int:
    """Total estimated RAM for all local models if loaded simultaneously."""
    return sum(m.ram_mb for m in MODEL_CATALOG.values() if m.is_local)


def estimate_eco_ram_mb(brain: str | None = None, worker: str | None = None) -> int:
    """Estimate RAM for a brain+worker combo."""
    defaults = MODE_MODELS["hybrid"]
    brain_profile = get_model(brain or defaults["brain"])
    worker_profile = get_model(worker or defaults["worker"])
    brain_ram = brain_profile.ram_mb if brain_profile else 0
    worker_ram = worker_profile.ram_mb if worker_profile else 0
    return brain_ram + worker_ram
