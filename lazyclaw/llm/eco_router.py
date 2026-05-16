"""ECO Router v5 — 3-mode architecture.

Three roles: Brain (= Team Lead), Worker, Fallback.
Three modes:
  HYBRID:  Sonnet brain + Gemma 4 E2B local worker ($0) + Haiku fallback (auto)
  FULL:    User-configurable brain/worker/fallback (paid, auto)
  CLAUDE:  Haiku API brain (native tools) + Claude CLI fallback ($0)

All model assignments come from MODE_MODELS in model_registry.py.
User overrides in eco_settings take priority over defaults.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.llm.free_providers import (
    PRIORITY_ORDER,
    PROVIDER_DEFS,
    FreeProviderResult,
    cascade_chat,
    discover_providers,
    stream_chat as free_stream_chat,
)
from lazyclaw.llm.model_registry import (
    MODE_MODELS,
    get_mode_models,
    RoutingResult,
    get_model,
)
from lazyclaw.llm.providers.base import LLMMessage, LLMResponse, StreamChunk
from lazyclaw.llm.rate_limiter import RateLimiter
from lazyclaw.llm.router import LLMRouter

logger = logging.getLogger(__name__)


# ── ECO Modes ─────────────────────────────────────────────────────────

MODE_HYBRID = "hybrid"   # Sonnet brain + Gemma 4 E2B local worker, auto-fallback
MODE_FULL = "full"       # User-configurable brain/worker/fallback (paid)
MODE_CLAUDE = "claude"   # All roles via claude -p CLI ($0 via subscription)
MODE_MINIMAX = "minimax" # MiniMax M2.7 brain + worker (Token Plan), Haiku fallback

# Legacy aliases — map old names to the supported modes
_MODE_ALIASES = {
    "hybrid": MODE_HYBRID,
    "full": MODE_FULL,
    "off": MODE_FULL,
    "claude": MODE_CLAUDE,
    "minimax": MODE_MINIMAX,
    "m2": MODE_MINIMAX,
    "m2.7": MODE_MINIMAX,
}

# Old eco/local modes are disabled (require 32GB+ RAM)
_DISABLED_MODES = frozenset({"local", "eco", "eco_on", "on"})

DISABLED_MODE_MESSAGE = (
    "ECO mode (local-only) requires 32GB+ RAM and is coming in a future update. "
    "Use HYBRID for the best balance of cost and quality."
)

VALID_MODES = frozenset({MODE_HYBRID, MODE_FULL, MODE_CLAUDE, MODE_MINIMAX})


# ── Claude CLI model validator ────────────────────────────────────────
# The CLI accepts:
#   - aliases: sonnet, opus, haiku
#   - full IDs: claude-sonnet-*, claude-opus-*, claude-haiku-* (with
#     optional [1m] suffix for the 1M-context tier)
# `claude-cli` is the INTERNAL registry alias (model_registry.py) and is
# not a valid --model value — it slipped into stored settings via the
# old ModelAssignment dropdown that listed every registry profile.
_CLI_MODEL_ALIASES = frozenset({"sonnet", "opus", "haiku"})
_CLI_MODEL_FAMILIES = ("sonnet", "opus", "haiku")


def _is_valid_cli_model(name: str) -> bool:
    """True if `name` is something `claude --model X` will accept."""
    if name in _CLI_MODEL_ALIASES:
        return True
    if name.startswith("claude-") and any(
        fam in name for fam in _CLI_MODEL_FAMILIES
    ):
        return True
    return False


def _resolve_claude_cli_model(settings: object | None, role: str) -> str:
    """Pick the right CLI model for a given role + settings, with strict
    fallback to claude-sonnet-4-6 when the stored value is junk.

    Role mapping:
      - ROLE_BRAIN (default) → claude_brain_model
      - ROLE_WORKER          → claude_worker_model, then claude_brain_model
      - any other (escalation, fallback) → claude_brain_model

    Junk values (e.g. the legacy registry alias "claude-cli" written by
    the old ModelAssignment dropdown) trigger a one-line warning and
    fall back to the safe default. Same pattern as the brain validator
    in commit 0ee38f3.
    """
    DEFAULT = "claude-sonnet-4-6"
    if settings is None:
        return DEFAULT

    if role == ROLE_WORKER:
        candidates = [
            ("claude_worker_model", getattr(settings, "claude_worker_model", None)),
            ("claude_brain_model", getattr(settings, "claude_brain_model", None)),
        ]
    else:
        candidates = [
            ("claude_brain_model", getattr(settings, "claude_brain_model", None)),
        ]

    for field_name, value in candidates:
        if not value:
            continue
        if _is_valid_cli_model(value):
            return value
        logger.warning(
            "%s=%r is not a valid Claude CLI model name — falling back. "
            "Re-pick a model in Settings → CLAUDE mode to clear this state.",
            field_name, value,
        )

    return DEFAULT

# Backward-compat aliases for imports that used the old names
MODE_ECO_ON = MODE_HYBRID      # deprecated
MODE_ECO_HYBRID = MODE_HYBRID  # deprecated
MODE_ECO_OFF = MODE_FULL       # deprecated


def normalize_mode(mode: str) -> str:
    """Normalize mode string to canonical form."""
    key = mode.lower().strip()
    if key in _DISABLED_MODES:
        return key  # caller should check and reject
    return _MODE_ALIASES.get(key, MODE_HYBRID)


# ── Request role (who's asking) ───────────────────────────────────────

ROLE_BRAIN = "brain"      # Chat, planning, synthesis — no tools
ROLE_WORKER = "worker"    # Tool execution — gets tools


# ── Complexity classification ─────────────────────────────────────────

COMPLEXITY_SIMPLE = "simple"
COMPLEXITY_STANDARD = "standard"
COMPLEXITY_COMPLEX = "complex"

_COMPLEX_PATTERNS = re.compile(
    r"\b(analyze|compare|plan|debug|research|investigate|evaluate|"
    r"architect|design|refactor|review|audit|benchmark|optimize|"
    r"explain.*code|trace.*bug|root.*cause)\b",
    re.IGNORECASE,
)

_SIMPLE_ACTION_PATTERN = re.compile(
    r"\b(search|browse|find|create|write|run|schedule|calculate|"
    r"check|read|remind|list|show|fetch|tell|what|where|is there|"
    r"open|look|see|get)\b",
    re.IGNORECASE,
)


def classify_complexity(message: str, has_tools: bool) -> str:
    """Fast heuristic for model tier routing. No LLM call needed."""
    if _COMPLEX_PATTERNS.search(message):
        return COMPLEXITY_COMPLEX

    if has_tools and _SIMPLE_ACTION_PATTERN.search(message) and len(message) < 120:
        return COMPLEXITY_SIMPLE

    if not has_tools and len(message) < 100:
        lower = message.lower().strip()
        if len(lower) < 40 or not _SIMPLE_ACTION_PATTERN.search(lower):
            return COMPLEXITY_SIMPLE

    return COMPLEXITY_STANDARD


def _extract_user_message(messages: list[LLMMessage]) -> str:
    """Extract the latest user message from the conversation."""
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return ""


# ── ECO Settings ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class EcoSettings:
    """User's ECO mode configuration."""

    mode: str = MODE_HYBRID
    show_badges: bool = True
    monthly_paid_budget: float = 0.0        # 0 = unlimited
    auto_fallback: bool = True              # Both modes auto-fallback
    max_workers: int = 10                   # Max concurrent workers
    brain_model: str | None = None          # Generic override (lowest priority)
    worker_model: str | None = None
    fallback_model: str | None = None
    # Per-mode overrides (highest priority)
    hybrid_brain_model: str | None = None
    hybrid_worker_model: str | None = None
    hybrid_fallback_model: str | None = None
    full_brain_model: str | None = None
    full_worker_model: str | None = None
    full_fallback_model: str | None = None
    claude_brain_model: str | None = None
    claude_worker_model: str | None = None
    claude_fallback_model: str | None = None
    # Transport sub-mode inside MODE_CLAUDE: "sdk" (official Agent SDK,
    # native tool_use protocol, default) | "cli" (legacy raw `claude -p`).
    # Both share the same subscription auth — only the wire-protocol and
    # message handling differ.
    claude_transport: str = "sdk"
    minimax_brain_model: str | None = None
    minimax_worker_model: str | None = None
    minimax_fallback_model: str | None = None
    locked_provider: str | None = None      # Lock to specific free provider
    allowed_providers: list[str] | None = None
    free_providers: list[str] | None = None
    preferred_free_model: str | None = None


def _parse_eco_settings(raw: str | None) -> EcoSettings:
    """Parse eco settings from user's settings JSON."""
    if not raw:
        return EcoSettings()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse eco settings JSON, using defaults", exc_info=True)
        return EcoSettings()

    eco = data.get("eco", {})
    if not isinstance(eco, dict):
        return EcoSettings()

    allowed = eco.get("allowed_providers")
    if allowed and not isinstance(allowed, list):
        allowed = None

    free_providers = eco.get("free_providers")
    if free_providers and not isinstance(free_providers, list):
        free_providers = None

    raw_mode = eco.get("mode", "hybrid")
    mode = normalize_mode(raw_mode)
    # Reject disabled modes — fall back to hybrid
    if mode in _DISABLED_MODES:
        mode = MODE_HYBRID

    # Strip junk claude_*_model values written by the old ModelAssignment
    # dropdown (legacy registry aliases like "claude-cli" / "claude_code"
    # are NOT valid `--model` arguments for the claude binary). Without
    # this coerce, every CLAUDE-mode turn logs a WARNING from
    # `_resolve_claude_cli_model` even though we fall back to a safe
    # default. Strip at parse time so the stored junk is invisible to
    # the rest of the router. Fixed-set tokens we know are not CLI models:
    _CLAUDE_JUNK_MODEL_NAMES = {"claude-cli", "claude-sdk", "claude_code", "claude"}

    def _clean_claude_model(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        if value in _CLAUDE_JUNK_MODEL_NAMES:
            return None
        return value or None

    return EcoSettings(
        mode=mode,
        show_badges=eco.get("show_badges", True),
        monthly_paid_budget=float(eco.get("monthly_paid_budget", 0)),
        auto_fallback=eco.get("auto_fallback", True),
        max_workers=int(eco.get("max_workers", 10)),
        brain_model=eco.get("brain_model"),
        worker_model=eco.get("worker_model") or eco.get("specialist_model"),
        fallback_model=eco.get("fallback_model"),
        hybrid_brain_model=eco.get("hybrid_brain_model"),
        hybrid_worker_model=eco.get("hybrid_worker_model"),
        hybrid_fallback_model=eco.get("hybrid_fallback_model"),
        full_brain_model=eco.get("full_brain_model"),
        full_worker_model=eco.get("full_worker_model"),
        full_fallback_model=eco.get("full_fallback_model"),
        claude_brain_model=_clean_claude_model(eco.get("claude_brain_model")),
        claude_worker_model=_clean_claude_model(eco.get("claude_worker_model")),
        claude_fallback_model=_clean_claude_model(eco.get("claude_fallback_model")),
        claude_transport=(
            eco.get("claude_transport")
            if eco.get("claude_transport") in ("sdk", "cli")
            else "sdk"
        ),
        minimax_brain_model=eco.get("minimax_brain_model"),
        minimax_worker_model=eco.get("minimax_worker_model"),
        minimax_fallback_model=eco.get("minimax_fallback_model"),
        locked_provider=eco.get("locked_provider"),
        allowed_providers=allowed,
        free_providers=free_providers,
        preferred_free_model=eco.get("preferred_free_model"),
    )


async def _load_eco_settings(config: Config, user_id: str) -> EcoSettings:
    """Load ECO settings from user's settings column."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT settings FROM users WHERE id = ?", (user_id,)
        )
        result = await row.fetchone()
    if not result or not result[0]:
        return EcoSettings()
    return _parse_eco_settings(result[0])


def _infer_paid_provider(model: str) -> str:
    """Infer paid provider name from model name."""
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith(("MiniMax-", "minimax-")):
        return "minimax"
    return "openai"


# 429 / quota / rate-limit signal substrings. MiniMax's Anthropic-compat
# layer surfaces the 5-hour quota cap as one of these — the exact wording
# varies by which proxy returns it (cloudflare 429, MiniMax adapter
# `quota_exhausted`, anthropic SDK `RateLimitError`). Substring match
# stays SDK-version agnostic.
_QUOTA_SIGNALS = (
    "429",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "quota",
    "too many requests",
    "exceeded",
)


def _is_quota_error(exc_str: str) -> bool:
    """True if the exception string looks like a 429 / quota cap."""
    s = exc_str.lower()
    return any(sig in s for sig in _QUOTA_SIGNALS)


# ── EcoRouter ─────────────────────────────────────────────────────────

class EcoRouter:
    """Routes requests between local (MLX/Ollama) and paid (Claude) providers.

    Core principle: Brain never gets tools. Workers always get tools.
    Brain decides WHAT to do. Workers execute HOW.

    Usage:
        eco = EcoRouter(config, paid_router)
        # Brain call (no tools)
        response = await eco.chat(messages, user_id, role="brain")
        # Worker call (with tools)
        response = await eco.chat(messages, user_id, role="worker", tools=[...])
        # Check attribution
        routing = eco.last_routing
    """

    def __init__(self, config: Config, paid_router: LLMRouter) -> None:
        self._config = config
        self._paid_router = paid_router
        self._rate_limiter = RateLimiter()
        # Apply MiniMax Token Plan tier to the limiter's 5h window so the
        # TUI shows the right cap (Plus=4500, Max=15000, etc.).
        tier = getattr(config, "minimax_token_plan_tier", "plus")
        try:
            self._rate_limiter.set_minimax_tier(tier)
        except Exception:  # pragma: no cover — defensive
            logger.warning("Invalid MiniMax tier %r — using plus default", tier)
        self._usage: dict[str, dict] = {}  # user_id → {local, free, paid}

        # Local providers (lazy init)
        self._mlx_brain = None      # MLXProvider for brain
        self._mlx_worker = None     # MLXProvider for worker
        self._ollama = None         # OllamaProvider fallback
        self._local_checked = False
        self._local_lock = asyncio.Lock()
        self._mlx_manager: Any | None = None  # MLXManager for on-demand

        # Free provider keys (lazy init)
        self._free_keys: dict[str, str] | None = None

        # Claude CLI provider (lazy init — legacy transport)
        self._claude_cli = None
        # Claude Agent SDK provider (lazy init — primary transport for MODE_CLAUDE)
        # Tracks the model the cached instance was built with so a runtime
        # model change triggers a rebuild (mirrors how _claude_cli._model is
        # re-assigned in place below).
        self._claude_sdk = None
        self._claude_sdk_model: str | None = None
        self._last_claude_fallback: str | None = None

        # Routing attribution — set after every chat() call
        self.last_routing: RoutingResult | None = None

        # Per-model stats for TUI
        self._routing_stats: dict[str, dict] = {}

    # ── Local provider management ─────────────────────────────────────

    async def _ensure_local(self) -> tuple:
        """Lazy-init local providers. Returns (brain_provider, worker_provider).

        HYBRID mode uses Ollama as the primary local worker (gemma4:e2b).
        Ollama delegates model management to its own server — no manual process
        lifecycle needed. MLX is checked as a secondary option for any users
        still running the legacy mlx_lm.server setup.

        Returns (None, None) if no local provider available.
        """
        # Fast path: already checked and Ollama is up
        if self._local_checked and self._ollama:
            return None, self._ollama

        async with self._local_lock:
            if self._local_checked and self._ollama:
                return None, self._ollama

            # Reset stale state
            self._mlx_brain = None
            self._mlx_worker = None

            # Primary: Ollama (handles Gemma 4 E2B via Metal backend)
            try:
                from lazyclaw.llm.providers.ollama_provider import OllamaProvider
                ollama = OllamaProvider()
                if await ollama.health_check():
                    self._ollama = ollama
                    logger.info("Ollama connected — gemma4:e2b worker ready")
            except Exception as exc:
                logger.debug("Ollama not available: %s", exc)

            # Secondary: legacy MLX direct servers (deprecated, for backward compat)
            if not self._ollama:
                try:
                    from lazyclaw.llm.providers.mlx_provider import MLXProvider  # noqa: deprecated

                    _eco_models = get_mode_models("hybrid")
                    _worker_model = _eco_models["worker"]
                    _brain_model = _eco_models["brain"]

                    worker = MLXProvider("http://127.0.0.1:8081")
                    if await worker.health_check():
                        worker._loaded_model = _worker_model
                        self._mlx_worker = worker
                        logger.info("MLX (legacy) on :8081 → %s", _worker_model)

                    brain = MLXProvider("http://127.0.0.1:8080")
                    if await brain.health_check():
                        brain._loaded_model = _brain_model
                        self._mlx_brain = brain
                        logger.info("MLX (legacy) on :8080 → %s", _brain_model)

                    if self._mlx_worker and not self._mlx_brain:
                        self._mlx_brain = self._mlx_worker
                    elif self._mlx_brain and not self._mlx_worker:
                        self._mlx_worker = self._mlx_brain
                except Exception as exc:
                    logger.debug("MLX not available: %s", exc)

            self._local_checked = True
            # Return: (brain, worker) — Ollama serves worker role
            if self._ollama:
                return None, self._ollama
            return self._mlx_brain, self._mlx_worker

    async def _ensure_ollama(self):
        """Return the Ollama provider if available, else None.

        Used by the TUI to show Ollama model status. Never raises.
        Caches the result — only re-checks after reset_local_check().
        """
        if self._ollama is not None:
            return self._ollama
        # Only try once per reset cycle to avoid health check spam.
        # After reset_local_check(), _ollama_checked resets to False.
        if getattr(self, "_ollama_checked", False):
            return None
        try:
            import asyncio as _aio
            from lazyclaw.llm.providers.ollama_provider import OllamaProvider
            candidate = OllamaProvider()
            try:
                is_healthy = await _aio.wait_for(
                    candidate.health_check(), timeout=3.0,
                )
            except _aio.TimeoutError:
                is_healthy = False
            if is_healthy:
                self._ollama = candidate
            else:
                await candidate.close()
            self._ollama_checked = True
            return self._ollama
        except Exception:
            logger.debug("Ollama availability check failed", exc_info=True)
            self._ollama_checked = True
            return None

    def reset_local_check(self) -> None:
        """Reset local provider detection (after user installs/restarts)."""
        self._mlx_brain = None
        self._mlx_worker = None
        self._ollama = None
        self._ollama_checked = False
        self._local_checked = False

    # ── Free provider management ──────────────────────────────────────

    def _get_free_keys(self) -> dict[str, str]:
        """Discover and cache free provider API keys from env."""
        if self._free_keys is None:
            self._free_keys = discover_providers()
            if self._free_keys:
                logger.info("Free providers: %s", ", ".join(self._free_keys))
        return self._free_keys

    def _get_provider_order(self, settings: EcoSettings) -> list[str]:
        """Ordered list of free providers to try."""
        keys = self._get_free_keys()
        if not keys:
            return []

        if settings.locked_provider and settings.locked_provider in keys:
            return [settings.locked_provider]

        if settings.free_providers:
            return [p for p in settings.free_providers if p in keys]

        return [p for p in PRIORITY_ORDER if p in keys]

    def refresh_free_keys(self) -> None:
        """Re-scan env vars for free provider keys."""
        self._free_keys = None

    # ── Message conversion (for free/local providers) ─────────────────

    @staticmethod
    def _convert_to_dicts(messages: list[LLMMessage]) -> list[dict]:
        """Convert LLMMessages to OpenAI-format dicts.

        Free/local providers without native tool support get tool
        messages converted to plain text.
        """
        result = []
        for msg in messages:
            if msg.role == "tool":
                result.append({
                    "role": "user",
                    "content": f"[Tool result: {msg.content}]",
                })
            elif msg.tool_calls:
                parts = []
                if msg.content:
                    parts.append(msg.content)
                for tc in msg.tool_calls:
                    parts.append(f"[Used tool: {tc.name}]")
                if parts:
                    result.append({"role": "assistant", "content": " ".join(parts)})
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result

    # ── Usage tracking ────────────────────────────────────────────────

    def _record_usage(self, user_id: str, route: str) -> None:
        """Track usage stats. Route: 'local', 'free', or 'paid'."""
        if user_id not in self._usage:
            self._usage[user_id] = {"local": 0, "free": 0, "paid": 0}
        self._usage[user_id][route] = self._usage[user_id].get(route, 0) + 1

    def _record_routing_stats(self, model: str, usage: dict | None) -> None:
        """Track per-model call stats for TUI routing panel."""
        if model not in self._routing_stats:
            self._routing_stats[model] = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        stats = self._routing_stats[model]
        stats["calls"] += 1
        if usage:
            stats["tokens_in"] += usage.get("prompt_tokens", 0)
            stats["tokens_out"] += usage.get("completion_tokens", 0)

    def _set_routing(
        self, model: str, provider: str, is_local: bool, reason: str
    ) -> None:
        """Set last_routing attribution."""
        self.last_routing = RoutingResult(
            model=model, provider=provider, is_local=is_local, reason=reason,
        )

    # ── Resolve models for current mode ─────────────────────────────

    def _resolve_models(self, settings: EcoSettings) -> dict[str, str]:
        """Get brain/worker/fallback model IDs for the current mode.

        Priority: per-mode override > generic override > MODE_MODELS default.
        Each mode is fully isolated — setting HYBRID worker won't affect FULL.
        """
        defaults = get_mode_models(settings.mode)
        mode = settings.mode
        # Per-mode fields: {mode}_{role}_model
        mode_brain = getattr(settings, f"{mode}_brain_model", None)
        mode_worker = getattr(settings, f"{mode}_worker_model", None)
        mode_fallback = getattr(settings, f"{mode}_fallback_model", None)
        return {
            "brain": mode_brain or settings.brain_model or defaults["brain"],
            "worker": mode_worker or settings.worker_model or defaults["worker"],
            "fallback": mode_fallback or settings.fallback_model or defaults["fallback"],
        }

    def _is_auto_fallback(self, settings: EcoSettings) -> bool:
        """Both HYBRID and FULL always auto-fallback."""
        return True

    async def get_fallback_model(self, user_id: str) -> str:
        """Public: return the fallback model name for this user's current mode.

        Used by callers (Agent) to force-escalate when the brain/worker
        returns empty responses repeatedly (e.g. MiniMax error 2013).
        """
        settings = await _load_eco_settings(self._config, user_id)
        models = self._resolve_models(settings)
        return models["fallback"]

    # ── Main chat router ──────────────────────────────────────────────

    async def chat(
        self,
        messages: list[LLMMessage],
        user_id: str,
        model: str | None = None,
        role: str = ROLE_BRAIN,
        **kwargs,
    ) -> LLMResponse:
        """Route chat based on ECO mode and request role.

        Args:
            messages: Conversation messages.
            user_id: User ID for settings lookup.
            model: Explicit model override (bypasses routing).
            role: ROLE_BRAIN or ROLE_WORKER.
            **kwargs: tools, tool_choice, temperature, etc.
        """
        settings = await _load_eco_settings(self._config, user_id)
        models = self._resolve_models(settings)

        # MODE_CLAUDE is strictly sticky: every brain/worker/escalation/
        # explicit-model-override path goes through the user's Claude
        # subscription. The user runs CLAUDE mode precisely because they
        # have $0 paid-API balance, so silently falling back to
        # _route_paid would always 400. One chokepoint here captures
        # every entry path — including agent.py's `role="escalation"`
        # recovery path that previously bypassed routing at line 559.
        #
        # Inside MODE_CLAUDE the transport sub-mode selects how we talk
        # to `claude`:
        #   - "sdk" (default) → official claude-agent-sdk, native tool_use
        #   - "cli"           → legacy raw `claude -p` subprocess
        # SDK failures auto-cascade to CLI only on SDKUnavailable (binary
        # missing, auth missing, package missing). Real upstream errors
        # surface to the caller.
        if settings.mode == MODE_CLAUDE:
            transport = getattr(settings, "claude_transport", "sdk") or "sdk"
            if transport == "sdk":
                try:
                    return await self._route_claude_sdk(
                        messages, user_id, settings=settings, role=role, **kwargs,
                    )
                except Exception as exc:
                    # Narrow auto-fallback: only SDKUnavailable. Anything
                    # else (auth, API, tool dispatch) should surface so
                    # we don't mask real bugs by silently dropping to CLI.
                    from lazyclaw.llm.providers.claude_sdk_provider import (
                        SDKUnavailable,
                    )
                    if isinstance(exc, SDKUnavailable):
                        logger.warning(
                            "Claude SDK unavailable, falling back to CLI: %s",
                            exc,
                        )
                        return await self._route_claude(
                            messages, user_id, settings=settings, role=role,
                            **kwargs,
                        )
                    raise
            return await self._route_claude(
                messages, user_id, settings=settings, role=role, **kwargs,
            )

        # Explicit model override — bypass routing
        if model and role not in (ROLE_BRAIN, ROLE_WORKER):
            # Subscription-only model names don't go through paid API —
            # route through the matching transport. Both names point at
            # the same Claude binary; "sdk" gets the native protocol.
            if model == "claude-sdk":
                return await self._route_claude_sdk(
                    messages, user_id, settings=settings, **kwargs,
                )
            if model == "claude-cli":
                return await self._route_claude(
                    messages, user_id, settings=settings, **kwargs,
                )
            return await self._route_paid(messages, user_id, model, **kwargs)

        if role == ROLE_BRAIN:
            return await self._route_brain(
                messages, user_id, settings, models, **kwargs
            )

        if role == ROLE_WORKER:
            return await self._route_worker(
                messages, user_id, settings, models, **kwargs
            )

        # Unknown role — default to brain
        return await self._route_brain(
            messages, user_id, settings, models, **kwargs
        )

    # ── Brain routing (same for all modes — just picks the right model)

    async def _route_brain(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        models: dict[str, str],
        **kwargs,
    ) -> LLMResponse:
        """Brain: paid API or local Ollama (user-configurable).

        Falls back to Claude CLI on auth errors (401) — prevents dead
        agent when API key is expired but Claude subscription works.
        """
        brain_name = models["brain"]
        brain_profile = get_model(brain_name)
        is_local_brain = brain_profile and brain_profile.is_local

        # CLI/MCP model names can't go through paid API — route to CLI
        if brain_name in ("claude-cli", "claude_code"):
            return await self._route_claude(
                messages, user_id, settings=settings, **kwargs,
            )

        # Local brain (user chose a local model like gemma4:e4b)
        if is_local_brain:
            _, worker_provider = await self._ensure_local()
            provider = worker_provider or self._ollama
            if provider:
                try:
                    return await self._call_local(
                        provider, messages, brain_name, user_id,
                        reason=f"{settings.mode}: brain -> {brain_name}",
                        **kwargs,
                    )
                except Exception as exc:
                    logger.warning("%s local brain failed: %s — falling back", settings.mode, exc)
            # Local failed — fallback
            return await self._fallback(
                messages, user_id, settings, models,
                reason=f"{settings.mode}: local_brain_failed",
                **kwargs,
            )

        # Paid brain (default path)
        try:
            return await self._route_paid(
                messages, user_id, brain_name,
                reason=f"{settings.mode}: brain -> {brain_name}",
                **kwargs,
            )
        except Exception as exc:
            exc_str = str(exc)
            if "401" in exc_str or "authentication" in exc_str.lower():
                logger.warning(
                    "Paid brain 401 — falling back to Claude CLI: %s", exc,
                )
                resp = await self._route_claude(
                    messages, user_id, settings=settings, **kwargs,
                )
                resp.fallback_reason = "auth"
                return resp
            if "529" in exc_str or "overloaded" in exc_str.lower():
                logger.warning(
                    "Paid brain 529 overloaded — falling back to Claude CLI: %s", exc,
                )
                resp = await self._route_claude(
                    messages, user_id, settings=settings, **kwargs,
                )
                resp.fallback_reason = "overloaded"
                return resp
            # MiniMax 5-hour quota exhausted — fall to mode's fallback model
            # (Haiku for mode=minimax). Block further minimax calls until the
            # rolling window opens by saturating the rate-limiter window.
            if _is_quota_error(exc_str) and _infer_paid_provider(brain_name) == "minimax":
                logger.warning(
                    "MiniMax quota exhausted (brain) — falling back to %s: %s",
                    models["fallback"], exc,
                )
                self._rate_limiter.record_rate_limit_hit("minimax")
                resp = await self._fallback(
                    messages, user_id, settings, models,
                    reason=f"{settings.mode}: minimax_quota -> {models['fallback']}",
                    **kwargs,
                )
                resp.fallback_reason = "quota"
                return resp
            raise

    # ── Claude CLI routing (all roles through claude -p) ───────────────

    async def _route_claude(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings | None = None,
        role: str = ROLE_BRAIN,
        **kwargs,
    ) -> LLMResponse:
        """Route through Claude CLI ($0 via subscription).

        Strictly the only LLM provider for MODE_CLAUDE — no paid-API
        fallback, since the user runs this mode precisely because they
        have no API credit. CLI failures surface clear actionable error
        messages instead of cascading to a paid call that always 400s.
        """
        # Per-mode fields are role-scoped: brain → claude_brain_model,
        # worker → claude_worker_model (falls back to brain), and any
        # other role uses the brain field. Validator catches legacy
        # junk like the registry alias "claude-cli".
        cli_model = _resolve_claude_cli_model(settings, role)

        if self._claude_cli is None:
            from lazyclaw.llm.providers.claude_cli_provider import (
                ClaudeCLIProvider,
            )
            self._claude_cli = ClaudeCLIProvider(model=cli_model)
        else:
            self._claude_cli._model = cli_model

        self._set_routing(
            "claude-cli", "claude_cli", is_local=False,
            reason=f"claude: {role} -> {cli_model}",
        )
        self._record_usage(user_id, "free")

        try:
            response = await self._claude_cli.chat(messages, model="claude-cli", **kwargs)
        except Exception as exc:
            logger.warning("Claude CLI failed: %s", exc)
            self._last_claude_fallback = str(exc)

            # MODE_CLAUDE is strict: NO paid-API fallback. The user is
            # here precisely because they have $0 API credit, so a
            # cascade to _route_paid would always 400. Instead, surface
            # a clear actionable message keyed off the failure reason.
            cli_err = str(exc)
            cli_err_l = cli_err.lower()

            if "not logged in" in cli_err_l or "/login" in cli_err_l:
                msg = (
                    "Claude CLI is not logged in — MODE_CLAUDE can't reach "
                    "your subscription. Run `make claude-login` inside the "
                    "lazyclaw container (or `claude /login` on the host) "
                    "to authenticate, then retry."
                )
            elif "timed out" in cli_err_l or "timeout" in cli_err_l:
                msg = (
                    f"Claude CLI timed out (model={cli_model}). Try a "
                    f"faster model in Settings → CLAUDE mode (Sonnet 4.6 "
                    f"is fastest), or send a shorter message."
                )
            elif "not found" in cli_err_l or "no such file" in cli_err_l:
                msg = (
                    "Claude CLI binary not found in the container. "
                    "Run `make rebuild` to reinstall."
                )
            else:
                msg = (
                    f"Claude CLI failed: {exc}. Re-pick a model in "
                    f"Settings → CLAUDE mode and retry. Or switch to "
                    f"HYBRID/FULL mode if you have paid-API credit."
                )

            return LLMResponse(content=msg, model="error", tool_calls=[])

        self._last_claude_fallback = None
        self._record_routing_stats("claude-cli", response.usage)
        return response

    # ── Claude Agent SDK routing (MODE_CLAUDE transport=sdk) ───────────

    async def _route_claude_sdk(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings | None = None,
        role: str = ROLE_BRAIN,
        **kwargs,
    ) -> LLMResponse:
        """Route through the official `claude-agent-sdk` ($0 via subscription).

        Native tool_use protocol, typed AssistantMessage/ResultMessage,
        accurate cost via `ResultMessage.total_cost_usd`. Same
        subscription auth as the legacy CLI path (both shell into the
        same `claude` binary).

        Failures policy:
          - `SDKUnavailable` propagates up to the MODE_CLAUDE dispatcher,
            which catches it and falls back to the CLI transport (binary
            missing, package missing, not logged in).
          - All other exceptions (upstream API error, tool dispatch
            failure, timeout) propagate to the caller — they're real bugs
            and should surface, not be masked by a silent CLI fallback.
        """
        # Same model resolver as the CLI path — model names are identical
        # (`sonnet` / `opus` / `haiku` / full IDs) since both transports
        # invoke the same `claude` binary.
        sdk_model = _resolve_claude_cli_model(settings, role)

        if self._claude_sdk is None or self._claude_sdk_model != sdk_model:
            from lazyclaw.llm.providers.claude_sdk_provider import (
                ClaudeSDKProvider,
            )
            self._claude_sdk = ClaudeSDKProvider(model=sdk_model)
            self._claude_sdk_model = sdk_model
        else:
            self._claude_sdk._model = sdk_model

        self._set_routing(
            "claude-sdk", "claude_sdk", is_local=False,
            reason=f"claude: {role} -> {sdk_model} (sdk)",
        )
        self._record_usage(user_id, "free")

        # SDKUnavailable bubbles up to the MODE_CLAUDE dispatcher which
        # falls back to the CLI transport. Other exceptions propagate.
        response = await self._claude_sdk.chat(
            messages, model="claude-sdk", **kwargs,
        )

        self._last_claude_fallback = None
        self._record_routing_stats("claude-sdk", response.usage)
        return response

    # ── Worker routing ────────────────────────────────────────────────

    async def _route_worker(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        models: dict[str, str],
        **kwargs,
    ) -> LLMResponse:
        """Worker: local first (HYBRID), paid (FULL), fallback cascade."""
        worker_name = models["worker"]
        worker_profile = get_model(worker_name)
        is_local_worker = worker_profile and worker_profile.is_local

        # CLI/MCP model names can't go through paid API — route to CLI
        if worker_name in ("claude-cli", "claude_code"):
            return await self._route_claude(
                messages, user_id, settings=settings, role=ROLE_WORKER,
                **kwargs,
            )

        # FULL mode: worker is paid (Haiku) — go straight to API.
        # Falls back to Claude CLI on auth errors (401).
        if not is_local_worker:
            try:
                return await self._route_paid(
                    messages, user_id, worker_name,
                    reason=f"{settings.mode}: worker -> {worker_name}",
                    **kwargs,
                )
            except Exception as exc:
                exc_str = str(exc)
                # MiniMax 5-hour quota — fallback to mode's fallback model
                # (typically Haiku) instead of going Claude CLI. Saturates
                # the limiter window to suppress retry storms.
                if _is_quota_error(exc_str) and _infer_paid_provider(worker_name) == "minimax":
                    logger.warning(
                        "MiniMax quota exhausted (worker) — falling back to %s: %s",
                        models["fallback"], exc,
                    )
                    self._rate_limiter.record_rate_limit_hit("minimax")
                    return await self._fallback(
                        messages, user_id, settings, models,
                        reason=f"{settings.mode}: minimax_quota_worker -> {models['fallback']}",
                        **kwargs,
                    )
                if "401" in exc_str or "authentication" in exc_str.lower() or "529" in exc_str or "overloaded" in exc_str.lower():
                    logger.warning(
                        "Paid worker error — falling back to Claude CLI: %s", exc,
                    )
                    return await self._route_claude(
                        messages, user_id, settings=settings, role=ROLE_WORKER,
                        **kwargs,
                    )
                raise

        # HYBRID: try local Gemma 4 first
        _, worker_provider = await self._ensure_local()
        provider = worker_provider or self._ollama
        if provider:
            try:
                return await self._call_local(
                    provider, messages, worker_name, user_id,
                    reason=f"{settings.mode}: worker -> {worker_name}",
                    **kwargs,
                )
            except Exception as exc:
                logger.warning("%s worker failed: %s — trying free", settings.mode, exc)

        # Local failed — try free providers
        free_resp = await self._try_free(messages, user_id, settings, **kwargs)
        if free_resp:
            return free_resp

        # All free exhausted — fallback
        return await self._fallback(
            messages, user_id, settings, models,
            reason=f"{settings.mode}: worker_failed",
            **kwargs,
        )

    # ── Paid call helper ──────────────────────────────────────────────

    async def _route_paid(
        self,
        messages: list[LLMMessage],
        user_id: str,
        model: str,
        reason: str = "paid",
        **kwargs,
    ) -> LLMResponse:
        """Route to paid provider (Claude/OpenAI/MiniMax)."""
        # Guard: non-API models must not reach paid routing
        if model in ("claude-cli", "claude_code"):
            raise ValueError(f"Cannot route non-API model to paid provider: {model}")
        provider = _infer_paid_provider(model)
        self._set_routing(model, provider, is_local=False, reason=reason)
        self._record_usage(user_id, "paid")

        response = await self._paid_router.chat(
            messages, model=model, user_id=user_id, **kwargs
        )
        # MiniMax Token Plan is request-count metered (4,500 / 5h on Plus).
        # Record every successful call so the TUI/limiter has a live count
        # and pre-emptively blocks before the proxy returns 429.
        if provider == "minimax":
            self._rate_limiter.record_request("minimax")
        self._record_routing_stats(model, response.usage)
        return response

    # ── Local call helper ─────────────────────────────────────────────

    async def _call_local(
        self,
        provider,
        messages: list[LLMMessage],
        model: str,
        user_id: str,
        reason: str = "local",
        **kwargs,
    ) -> LLMResponse:
        """Call a local provider (MLX or Ollama).

        On failure, resets local cache and raises so caller can fallback.
        """
        # Detect provider type by class name (avoids circular imports)
        provider_class = type(provider).__name__
        if provider_class == "OllamaProvider":
            provider_name = "ollama"
        elif provider_class == "MLXProvider":
            provider_name = "mlx"
        else:
            provider_name = "local"

        self._set_routing(model, provider_name, is_local=True, reason=reason)
        self._record_usage(user_id, "local")

        try:
            response = await asyncio.wait_for(
                provider.chat(messages, model=model, **kwargs),
                timeout=120,  # 2min max for local models
            )
            self._record_routing_stats(model, response.usage)
            return response
        except asyncio.TimeoutError:
            logger.warning("Local model %s timed out (>120s) — resetting cache", model)
            self.reset_local_check()
            raise
        except Exception as exc:
            logger.warning("Local model %s failed: %s — resetting cache", model, exc)
            # Reset so next call re-detects servers (maybe one crashed)
            self.reset_local_check()
            raise

    # ── Fallback (local failed → paid with permission) ────────────────

    async def _fallback(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        models: dict[str, str],
        reason: str = "fallback",
        **kwargs,
    ) -> LLMResponse:
        """Fallback to paid when local/worker fails. Always auto-fallback."""
        fallback_name = models["fallback"]
        logger.info("Auto-fallback to %s: %s", fallback_name, reason)
        # Short machine tag for UIs — extracted from the reason prefix.
        tag = (
            "local_failed" if "local_" in reason
            else "worker_failed" if "worker" in reason
            else "fallback"
        )
        # Claude CLI fallback — route through CLI provider ($0)
        if fallback_name == "claude-cli":
            resp = await self._route_claude(
                messages, user_id, settings=settings, **kwargs,
            )
            resp.fallback_reason = tag
            return resp
        resp = await self._route_paid(
            messages, user_id, fallback_name,
            reason=f"auto_fallback: {reason}",
            **kwargs,
        )
        resp.fallback_reason = tag
        return resp

    # ── Free provider helper ──────────────────────────────────────────

    async def _try_free(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        **kwargs,
    ) -> LLMResponse | None:
        """Try free providers. Returns None if all fail."""
        keys = self._get_free_keys()
        if not keys:
            return None

        order = self._get_provider_order(settings)
        available = [p for p in order if self._rate_limiter.has_capacity(p)]
        if not available:
            return None

        dict_messages = self._convert_to_dicts(messages)

        try:
            result = await cascade_chat(
                messages=dict_messages,
                provider_order=available,
                api_keys=keys,
                preferred_model=settings.preferred_free_model,
            )
        except RuntimeError:
            logger.warning("All free providers failed", exc_info=True)
            return None

        self._rate_limiter.record_request(result.provider)
        self._record_usage(user_id, "free")
        self._set_routing(
            result.model, result.provider, is_local=False,
            reason=f"free: {result.provider}/{result.model}",
        )
        self._record_routing_stats(result.model, result.usage)

        content = result.content
        if settings.show_badges:
            mode_labels = {MODE_HYBRID: "HYBRID", MODE_FULL: "FULL", MODE_CLAUDE: "CLAUDE"}
            mode_label = mode_labels.get(settings.mode, settings.mode.upper())
            content = f"[{mode_label} {result.provider}] {content}"

        return LLMResponse(
            content=content,
            model=result.model,
            usage={
                **result.usage,
                "provider": result.provider,
                "eco_mode": settings.mode,
            },
        )

    # ── Streaming ─────────────────────────────────────────────────────

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        user_id: str,
        model: str | None = None,
        role: str = ROLE_BRAIN,
        **kwargs,
    ):
        """Stream chat responses. Routes based on ECO mode + role."""
        settings = await _load_eco_settings(self._config, user_id)
        models = self._resolve_models(settings)

        # MODE_CLAUDE strict-sticky — every streaming call goes through
        # the SDK (default) or CLI subprocess. NEVER falls through to
        # the paid Anthropic API: user is in this mode precisely because
        # they have $0 paid-API credit and a fall-through would 400 with
        # "credit balance too low".
        #
        # SDK transport now does REAL token streaming via
        # ClaudeSDKProvider.stream_chat — each AssistantMessage TextBlock
        # surfaces as its own chunk so the Web UI sees text incrementally
        # instead of waiting 60–120s for the whole turn (legacy v1
        # behavior). CLI transport stays non-streaming (CLI provider
        # doesn't expose chunk-by-chunk output today).
        if settings.mode == MODE_CLAUDE:
            transport = getattr(settings, "claude_transport", "sdk") or "sdk"
            if transport == "sdk":
                # Lazy provider construction mirrors _route_claude_sdk.
                sdk_model = _resolve_claude_cli_model(settings, role)
                if (
                    self._claude_sdk is None
                    or self._claude_sdk_model != sdk_model
                ):
                    from lazyclaw.llm.providers.claude_sdk_provider import (
                        ClaudeSDKProvider,
                    )
                    self._claude_sdk = ClaudeSDKProvider(model=sdk_model)
                    self._claude_sdk_model = sdk_model
                else:
                    self._claude_sdk._model = sdk_model

                self._set_routing(
                    "claude-sdk", "claude_sdk", is_local=False,
                    reason=f"claude_stream: {role} -> {sdk_model} (sdk)",
                )
                self._record_usage(user_id, "free")

                try:
                    async for chunk in self._claude_sdk.stream_chat(
                        messages, model="claude-sdk", **kwargs,
                    ):
                        yield chunk
                    return
                except Exception as exc:
                    # Narrow auto-fallback to CLI: only SDKUnavailable.
                    # Anything else surfaces so we don't mask real bugs.
                    from lazyclaw.llm.providers.claude_sdk_provider import (
                        SDKUnavailable,
                    )
                    if not isinstance(exc, SDKUnavailable):
                        raise
                    logger.warning(
                        "Claude SDK stream unavailable, CLI fallback: %s",
                        exc,
                    )
                    # Fall through to CLI path below.

            # CLI transport (or SDK fallback) — collapse to one chunk
            # because the CLI provider doesn't expose token streaming.
            response = await self._route_claude(
                messages, user_id, settings=settings, role=role, **kwargs,
            )
            yield StreamChunk(
                delta=response.content,
                tool_calls=response.tool_calls,
                usage=response.usage,
                model=response.model,
                done=True,
            )
            return

        if role == ROLE_BRAIN:
            # Brain: paid streaming, or CLI fallback for non-API models.
            brain_name = models["brain"]

            # Non-API models (CLI/MCP) — use non-streaming CLI fallback
            if brain_name in ("claude-cli", "claude_code"):
                response = await self._route_claude(
                    messages, user_id, settings=settings, role=role, **kwargs,
                )
                yield StreamChunk(
                    delta=response.content,
                    tool_calls=response.tool_calls,
                    usage=response.usage,
                    model=response.model,
                    done=True,
                )
                return

            # Local brain streaming (user chose a local model)
            brain_profile = get_model(brain_name)
            is_local_brain = brain_profile and brain_profile.is_local
            if is_local_brain:
                _, worker_provider = await self._ensure_local()
                provider = worker_provider or self._ollama
                if provider:
                    self._set_routing(
                        brain_name, "ollama", is_local=True,
                        reason=f"{settings.mode}_stream: brain -> {brain_name}",
                    )
                    try:
                        async for chunk in provider.stream_chat(
                            messages, model=brain_name, **kwargs
                        ):
                            yield chunk
                    except Exception as exc:
                        logger.warning("Local brain stream failed: %s — non-streaming fallback", exc)
                        response = await self._fallback(
                            messages, user_id, settings, models,
                            reason=f"{settings.mode}: local_brain_stream_failed",
                            **kwargs,
                        )
                        yield StreamChunk(
                            delta=response.content,
                            tool_calls=response.tool_calls,
                            usage=response.usage,
                            model=response.model,
                            done=True,
                        )
                    return

            # Paid brain streaming (default)
            self._record_usage(user_id, "paid")
            provider = _infer_paid_provider(brain_name)
            self._set_routing(
                brain_name, provider, is_local=False,
                reason=f"{settings.mode}_stream: brain",
            )
            try:
                async for chunk in self._paid_router.stream_chat(
                    messages, model=brain_name, user_id=user_id, **kwargs
                ):
                    yield chunk
            except Exception as exc:
                exc_str = str(exc)
                if "401" in exc_str or "authentication" in exc_str.lower() or "529" in exc_str or "overloaded" in exc_str.lower():
                    logger.warning(
                        "Paid stream error — falling back to Claude CLI: %s", exc,
                    )
                    response = await self._route_claude(
                        messages, user_id, settings=settings, role=role, **kwargs,
                    )
                    yield StreamChunk(
                        delta=response.content,
                        tool_calls=response.tool_calls,
                        usage=response.usage,
                        model=response.model,
                        done=True,
                    )
                else:
                    raise
            return

        # Worker streaming
        worker_name = models["worker"]
        worker_profile = get_model(worker_name)
        is_local_worker = worker_profile and worker_profile.is_local

        # FULL mode: paid worker streaming.
        # Falls back to Claude CLI on auth errors (401).
        if not is_local_worker:
            self._record_usage(user_id, "paid")
            provider = _infer_paid_provider(worker_name)
            self._set_routing(
                worker_name, provider, is_local=False,
                reason=f"{settings.mode}_stream: worker",
            )
            try:
                async for chunk in self._paid_router.stream_chat(
                    messages, model=worker_name, user_id=user_id, **kwargs
                ):
                    yield chunk
            except Exception as exc:
                exc_str = str(exc)
                if "401" in exc_str or "authentication" in exc_str.lower() or "529" in exc_str or "overloaded" in exc_str.lower():
                    logger.warning(
                        "Paid worker stream error — falling back to CLI: %s", exc,
                    )
                    response = await self._route_claude(
                        messages, user_id, settings=settings, role=ROLE_WORKER,
                        **kwargs,
                    )
                    yield StreamChunk(
                        delta=response.content,
                        tool_calls=response.tool_calls,
                        usage=response.usage,
                        model=response.model,
                        done=True,
                    )
                else:
                    raise
            return

        # HYBRID: local worker streaming (Gemma 4)
        _, worker_provider = await self._ensure_local()
        provider = worker_provider or self._ollama
        if provider:
            try:
                async for chunk in provider.stream_chat(
                    messages, model=worker_name, **kwargs
                ):
                    yield chunk
                self._record_usage(user_id, "local")
                self._set_routing(
                    worker_name, "mlx", is_local=True,
                    reason=f"{settings.mode}_stream: worker",
                )
                return
            except Exception as exc:
                logger.warning("Local worker stream failed: %s", exc)

        # Fallback
        response = await self._fallback(
            messages, user_id, settings, models,
            reason=f"{settings.mode}_stream_fallback", **kwargs,
        )
        yield StreamChunk(
            delta=response.content, model=response.model, done=True,
        )

    # ── Stats ─────────────────────────────────────────────────────────

    def get_usage(self, user_id: str) -> dict:
        """Get usage stats for a user."""
        stats = self._usage.get(user_id, {"local": 0, "free": 0, "paid": 0})
        local = stats.get("local", 0)
        free = stats.get("free", 0)
        paid = stats.get("paid", 0)
        total = local + free + paid
        return {
            "local_count": local,
            "free_count": free,
            "paid_count": paid,
            "total": total,
            "local_percentage": round((local + free) / total * 100, 1) if total > 0 else 0,
            "paid_percentage": round(paid / total * 100, 1) if total > 0 else 0,
        }

    def get_routing_stats(self) -> dict:
        """Get per-model routing stats for TUI display."""
        from lazyclaw.llm.pricing import calculate_cost

        result = {}
        total_calls = 0
        total_cost = 0.0

        for model_name, stats in self._routing_stats.items():
            profile = get_model(model_name)
            cost = calculate_cost(model_name, stats["tokens_in"], stats["tokens_out"])
            total_calls += stats["calls"]
            total_cost += cost
            result[model_name] = {
                "calls": stats["calls"],
                "cost": cost,
                "icon": profile.icon if profile else "\U0001f916",
                "is_local": profile.is_local if profile else False,
                "display_name": profile.display_name if profile else model_name,
            }

        local_calls = sum(
            s["calls"]
            for m, s in self._routing_stats.items()
            if (p := get_model(m)) and p.is_local
        )

        return {
            "models": result,
            "total_cost": total_cost,
            "total_calls": total_calls,
            "local_pct": round(local_calls / total_calls * 100) if total_calls > 0 else 0,
        }

    def get_rate_limit_status(self) -> dict:
        """Get current rate limit status for all providers."""
        return self._rate_limiter.get_status()

    def get_free_provider_status(self) -> list[dict]:
        """Get status of all free providers."""
        from lazyclaw.llm.free_providers import get_provider_info

        info = get_provider_info()
        rate_status = self._rate_limiter.get_status()

        for item in info:
            name = item["name"]
            rs = rate_status.get(name, {})
            item["rate_limit_used"] = rs.get("minute_used", 0)
            item["rate_limit_max"] = rs.get("minute_max", 0)
            item["has_capacity"] = rs.get("has_capacity", True)
        return info

    async def get_mode_display(self, user_id: str) -> dict:
        """Get current mode info for display in Telegram/TUI."""
        settings = await _load_eco_settings(self._config, user_id)
        models = self._resolve_models(settings)

        brain_provider, worker_provider = await self._ensure_local()
        mlx_available = brain_provider is not None
        ollama_available = self._ollama is not None

        mode_labels = {
            MODE_HYBRID: "HYBRID",
            MODE_FULL: "FULL",
            MODE_CLAUDE: "CLAUDE",
        }

        return {
            "mode": settings.mode,
            "mode_label": mode_labels.get(settings.mode, settings.mode),
            "brain_model": models["brain"],
            "worker_model": models["worker"],
            "fallback_model": models["fallback"],
            "max_workers": settings.max_workers,
            "auto_fallback": self._is_auto_fallback(settings),
            "mlx_available": mlx_available,
            "ollama_available": ollama_available,
            "free_providers": list(self._get_free_keys().keys()),
            "usage": self.get_usage(user_id),
        }
