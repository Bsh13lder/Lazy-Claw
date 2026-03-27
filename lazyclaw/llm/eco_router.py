"""ECO Router v2 — Brain + Worker Pool architecture.

Three modes:
- ECO ON:     Local models only. Brain=Qwen3.5-9B, Workers=Nanbeige4.1-3B.
              Fallback to Claude Sonnet (with permission or auto).
- ECO HYBRID: Paid brain (Sonnet 4.6) + local workers (Nanbeige).
              Best quality brain, free execution.
- ECO OFF:    All paid. Brain=Sonnet 4.6, Workers=Haiku 4.5.
              Maximum quality, maximum cost.

Architecture:
  - Brain handles chat, planning, decomposition, synthesis. Never gets tools.
  - Workers handle tool execution. Up to 20 concurrent via asyncio.
  - Workers are generic — each picks its own tools from the registry.
  - stuck_detector decides when a worker is stuck, not a hardcoded counter.
  - Fallback to Claude Sonnet anywhere, with user permission or auto-approve.

Sits between agent.py and llm/router.py + mlx_provider.py.
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
    BRAIN_MODEL,
    WORKER_MODEL,
    FALLBACK_MODEL,
    PAID_BRAIN_MODEL,
    PAID_WORKER_MODEL,
    OLLAMA_BRAIN_MODEL,
    OLLAMA_WORKER_MODEL,
    RoutingResult,
    get_model,
)
from lazyclaw.llm.providers.base import LLMMessage, LLMResponse, StreamChunk
from lazyclaw.llm.rate_limiter import RateLimiter
from lazyclaw.llm.router import LLMRouter

logger = logging.getLogger(__name__)


# ── ECO Modes ─────────────────────────────────────────────────────────

MODE_ECO_ON = "eco_on"      # Local brain + local workers, $0
MODE_ECO_HYBRID = "hybrid"  # Paid brain + local workers
MODE_ECO_OFF = "off"        # All paid

# Legacy mode aliases (backward compat)
_MODE_ALIASES = {
    "local": MODE_ECO_ON,
    "eco": MODE_ECO_ON,
    "eco_on": MODE_ECO_ON,
    "on": MODE_ECO_ON,
    "hybrid": MODE_ECO_HYBRID,
    "full": MODE_ECO_OFF,
    "off": MODE_ECO_OFF,
}

VALID_MODES = frozenset({MODE_ECO_ON, MODE_ECO_HYBRID, MODE_ECO_OFF})


def normalize_mode(mode: str) -> str:
    """Normalize mode string to canonical form."""
    return _MODE_ALIASES.get(mode.lower().strip(), MODE_ECO_OFF)


# ── Request role (who's asking) ───────────────────────────────────────

ROLE_BRAIN = "brain"      # Chat, planning, synthesis — no tools
ROLE_WORKER = "worker"    # Tool execution — gets tools
ROLE_FALLBACK = "fallback"  # Paid fallback when local fails


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

    mode: str = MODE_ECO_OFF
    show_badges: bool = True
    monthly_paid_budget: float = 0.0        # 0 = unlimited
    auto_fallback: bool = False             # Auto-approve paid fallback
    max_workers: int = 10                   # Max concurrent workers
    brain_model: str | None = None          # Override brain (None = default)
    worker_model: str | None = None         # Override worker (None = default)
    fallback_model: str | None = None       # Override fallback (None = default)
    locked_provider: str | None = None      # Lock to specific free provider
    allowed_providers: list[str] | None = None
    free_providers: list[str] | None = None
    preferred_free_model: str | None = None
    # Legacy compat
    specialist_model: str | None = None
    task_overrides: dict[str, str] | None = None


def _parse_eco_settings(raw: str | None) -> EcoSettings:
    """Parse eco settings from user's settings JSON."""
    if not raw:
        return EcoSettings()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
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

    task_overrides = eco.get("task_overrides")
    if task_overrides and not isinstance(task_overrides, dict):
        task_overrides = None

    raw_mode = eco.get("mode", "off")
    mode = normalize_mode(raw_mode)

    return EcoSettings(
        mode=mode,
        show_badges=eco.get("show_badges", True),
        monthly_paid_budget=float(eco.get("monthly_paid_budget", 0)),
        auto_fallback=eco.get("auto_fallback", False),
        max_workers=int(eco.get("max_workers", 10)),
        brain_model=eco.get("brain_model"),
        worker_model=eco.get("worker_model") or eco.get("specialist_model"),
        fallback_model=eco.get("fallback_model"),
        locked_provider=eco.get("locked_provider"),
        allowed_providers=allowed,
        free_providers=free_providers,
        preferred_free_model=eco.get("preferred_free_model"),
        specialist_model=eco.get("specialist_model"),
        task_overrides=task_overrides,
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
        self._usage: dict[str, dict] = {}  # user_id → {local, free, paid}

        # Local providers (lazy init)
        self._mlx_brain = None      # MLXProvider for brain
        self._mlx_worker = None     # MLXProvider for worker
        self._ollama = None         # OllamaProvider fallback
        self._local_checked = False
        self._local_lock = asyncio.Lock()

        # Free provider keys (lazy init)
        self._free_keys: dict[str, str] | None = None

        # Routing attribution — set after every chat() call
        self.last_routing: RoutingResult | None = None

        # Per-model stats for TUI
        self._routing_stats: dict[str, dict] = {}

    # ── Local provider management ─────────────────────────────────────

    async def _ensure_local(self) -> tuple:
        """Lazy-init local providers. Returns (brain_provider, worker_provider).

        Tries MLX first (faster on Apple Silicon), falls back to Ollama.
        Returns (None, None) if no local provider available.
        """
        if self._local_checked:
            return self._mlx_brain, self._mlx_worker

        async with self._local_lock:
            if self._local_checked:
                return self._mlx_brain, self._mlx_worker

            # Try MLX — check both ports, use whatever is available
            try:
                from lazyclaw.llm.providers.mlx_provider import MLXProvider

                # Check :8081 first (worker/nanbeige — most common for single-model)
                worker = MLXProvider("http://127.0.0.1:8081")
                if await worker.health_check():
                    worker._loaded_model = WORKER_MODEL
                    self._mlx_worker = worker
                    logger.info("MLX connected on :8081 → %s", WORKER_MODEL)

                # Check :8080 (brain — only if dual-model setup)
                brain = MLXProvider("http://127.0.0.1:8080")
                if await brain.health_check():
                    brain._loaded_model = BRAIN_MODEL
                    self._mlx_brain = brain
                    logger.info("MLX connected on :8080 → %s", BRAIN_MODEL)

                # Single server: use one model for both roles
                if self._mlx_worker and not self._mlx_brain:
                    self._mlx_brain = self._mlx_worker
                    logger.info("MLX single-model: nanbeige serves brain + worker")
                elif self._mlx_brain and not self._mlx_worker:
                    self._mlx_worker = self._mlx_brain
                    logger.info("MLX single-model: brain serves both roles")
            except Exception as exc:
                logger.debug("MLX not available: %s", exc)

            # Ollama fallback (if MLX not available)
            if not self._mlx_brain:
                try:
                    from lazyclaw.llm.providers.ollama_provider import OllamaProvider
                    ollama = OllamaProvider()
                    if await ollama.health_check():
                        self._ollama = ollama
                        logger.info("Ollama connected (MLX not available)")
                except Exception as exc:
                    logger.debug("Ollama not available: %s", exc)

            self._local_checked = True
            return self._mlx_brain, self._mlx_worker

    def reset_local_check(self) -> None:
        """Reset local provider detection (after user installs/restarts)."""
        self._mlx_brain = None
        self._mlx_worker = None
        self._ollama = None
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
            role: ROLE_BRAIN (no tools), ROLE_WORKER (tools), ROLE_FALLBACK.
            **kwargs: tools, tool_choice, temperature, etc.
        """
        settings = await _load_eco_settings(self._config, user_id)

        # Explicit model override — bypass routing
        if model and role == ROLE_FALLBACK:
            return await self._route_paid(messages, user_id, model, **kwargs)

        if settings.mode == MODE_ECO_ON:
            return await self._route_eco_on(
                messages, user_id, settings, role, model, **kwargs
            )

        if settings.mode == MODE_ECO_HYBRID:
            return await self._route_hybrid(
                messages, user_id, settings, role, model, **kwargs
            )

        # ECO OFF — all paid
        return await self._route_eco_off(
            messages, user_id, settings, role, model, **kwargs
        )

    # ── ECO ON: Local brain + local workers ───────────────────────────

    async def _route_eco_on(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        role: str,
        model: str | None,
        **kwargs,
    ) -> LLMResponse:
        """ECO ON: Haiku brain (paid, cheap, instant) + local workers (Nanbeige, $0).

        Fallback to Sonnet if Haiku can't handle complexity.
        """
        brain_provider, worker_provider = await self._ensure_local()

        # Resolve models
        brain_name = settings.brain_model or BRAIN_MODEL
        worker_name = settings.worker_model or WORKER_MODEL

        if role == ROLE_BRAIN:
            # Brain: Haiku via API (instant, no thinking overhead)
            # Falls back to Sonnet if Haiku unavailable
            return await self._route_paid(
                messages, user_id, brain_name,
                reason=f"eco_on: brain -> {brain_name}",
                **kwargs,
            )

        if role == ROLE_WORKER:
            # Worker: Nanbeige local (tool calling champion, $0)
            provider = worker_provider or self._ollama
            if provider:
                try:
                    return await self._call_local(
                        provider, messages, worker_name, user_id,
                        reason=f"eco_on: worker -> {worker_name}",
                        **kwargs,
                    )
                except Exception as exc:
                    logger.warning("ECO ON worker failed: %s — trying free", exc)

            # No local worker or failed — try free providers
            free_resp = await self._try_free(messages, user_id, settings, **kwargs)
            if free_resp:
                return free_resp

            # No free either — fallback
            return await self._fallback(
                messages, user_id, settings,
                reason="eco_on: worker_failed",
                **kwargs,
            )

        # Unknown role — brain default
        return await self._route_eco_on(
            messages, user_id, settings, ROLE_BRAIN, model, **kwargs
        )

    # ── ECO HYBRID: Paid brain + local workers ────────────────────────

    async def _route_hybrid(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        role: str,
        model: str | None,
        **kwargs,
    ) -> LLMResponse:
        """HYBRID: Sonnet brain + local workers."""

        if role == ROLE_BRAIN:
            # Brain keeps tools (needs delegate, search_tools, etc.)
            brain_name = model or settings.brain_model or PAID_BRAIN_MODEL
            return await self._route_paid(
                messages, user_id, brain_name,
                reason=f"hybrid: paid_brain -> {brain_name}",
                **kwargs,
            )

        if role == ROLE_WORKER:
            # Worker: local first, then free, then paid fallback
            brain_provider, worker_provider = await self._ensure_local()
            worker_name = settings.worker_model or WORKER_MODEL
            provider = worker_provider or self._ollama

            if provider:
                try:
                    return await self._call_local(
                        provider, messages, worker_name, user_id,
                        reason=f"hybrid: local_worker -> {worker_name}",
                        **kwargs,
                    )
                except Exception as exc:
                    logger.warning("HYBRID worker failed: %s — trying free", exc)

            # Local unavailable or failed — try free providers
            free_resp = await self._try_free(messages, user_id, settings, **kwargs)
            if free_resp:
                return free_resp

            # All free exhausted — paid worker (Haiku, cheap)
            worker_paid = PAID_WORKER_MODEL
            return await self._route_paid(
                messages, user_id, worker_paid,
                reason=f"hybrid: paid_worker_fallback -> {worker_paid}",
                **kwargs,
            )

        return await self._route_hybrid(
            messages, user_id, settings, ROLE_BRAIN, model, **kwargs
        )

    # ── ECO OFF: All paid ─────────────────────────────────────────────

    async def _route_eco_off(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        role: str,
        model: str | None,
        **kwargs,
    ) -> LLMResponse:
        """ECO OFF: all paid. Sonnet brain, Haiku workers."""

        if role == ROLE_BRAIN:
            brain_name = model or settings.brain_model or PAID_BRAIN_MODEL
            return await self._route_paid(
                messages, user_id, brain_name,
                reason=f"off: brain -> {brain_name}",
                **kwargs,
            )

        if role == ROLE_WORKER:
            worker_name = model or settings.worker_model or PAID_WORKER_MODEL
            return await self._route_paid(
                messages, user_id, worker_name,
                reason=f"off: worker -> {worker_name}",
                **kwargs,
            )

        return await self._route_eco_off(
            messages, user_id, settings, ROLE_BRAIN, model, **kwargs
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
        """Route to paid provider (Claude/OpenAI)."""
        provider = "anthropic" if model.startswith("claude-") else "openai"
        self._set_routing(model, provider, is_local=False, reason=reason)
        self._record_usage(user_id, "paid")

        response = await self._paid_router.chat(
            messages, model=model, user_id=user_id, **kwargs
        )
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
        provider_name = "mlx"
        if hasattr(provider, '_base_url'):
            base = getattr(provider, '_base_url', '')
            if "8080" in base or "8081" in base:
                provider_name = "mlx"
        elif hasattr(provider, 'health_check') and not hasattr(provider, '_loaded_model'):
            provider_name = "ollama"

        self._set_routing(model, provider_name, is_local=True, reason=reason)
        self._record_usage(user_id, "local")

        try:
            response = await provider.chat(messages, model=model, **kwargs)
            self._record_routing_stats(model, response.usage)
            return response
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
        reason: str = "fallback",
        **kwargs,
    ) -> LLMResponse:
        """Fallback to paid when local fails.

        If auto_fallback is True, silently use Sonnet.
        If False, return a message asking the user to approve.
        """
        fallback_name = settings.fallback_model or FALLBACK_MODEL

        if settings.auto_fallback:
            logger.info("Auto-fallback to %s: %s", fallback_name, reason)
            return await self._route_paid(
                messages, user_id, fallback_name,
                reason=f"auto_fallback: {reason}",
                **kwargs,
            )

        # Ask user for permission (return info message)
        self._set_routing("none", "fallback", is_local=False, reason=f"ask_fallback: {reason}")
        profile = get_model(fallback_name)
        cost_hint = ""
        if profile:
            cost_hint = f" (~${profile.cost_input}/M input)"

        return LLMResponse(
            content=(
                f"Local model unavailable. Use {fallback_name}{cost_hint}?\n\n"
                f"Reply 'yes' to approve, or run:\n"
                f"  /eco auto on — auto-approve fallbacks\n"
                f"  /eco install — install local models"
            ),
            model="none",
        )

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
            content = f"[ECO {result.provider}] {content}"

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

        if settings.mode == MODE_ECO_ON:
            async for chunk in self._stream_eco_on(
                messages, user_id, settings, role, model, **kwargs
            ):
                yield chunk
            return

        if settings.mode == MODE_ECO_HYBRID:
            async for chunk in self._stream_hybrid(
                messages, user_id, settings, role, model, **kwargs
            ):
                yield chunk
            return

        # ECO OFF — paid streaming
        effective_model = model
        if role == ROLE_BRAIN:
            effective_model = model or settings.brain_model or PAID_BRAIN_MODEL
        elif role == ROLE_WORKER:
            effective_model = model or settings.worker_model or PAID_WORKER_MODEL

        self._record_usage(user_id, "paid")
        provider = "anthropic" if (effective_model or "").startswith("claude-") else "openai"
        self._set_routing(
            effective_model or PAID_BRAIN_MODEL, provider,
            is_local=False, reason=f"off_stream: {role}",
        )

        async for chunk in self._paid_router.stream_chat(
            messages, model=effective_model, user_id=user_id, **kwargs
        ):
            yield chunk

    async def _stream_eco_on(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        role: str,
        model: str | None,
        **kwargs,
    ):
        """ECO ON streaming: Haiku brain (paid stream) + local workers."""
        if role == ROLE_BRAIN:
            # Brain: Haiku via paid streaming (instant, no thinking)
            brain_name = settings.brain_model or BRAIN_MODEL
            self._record_usage(user_id, "paid")
            provider = "anthropic" if brain_name.startswith("claude-") else "openai"
            self._set_routing(
                brain_name, provider, is_local=False, reason="eco_on_stream: brain",
            )
            async for chunk in self._paid_router.stream_chat(
                messages, model=brain_name, user_id=user_id, **kwargs
            ):
                yield chunk
            return

        # Worker: local streaming (Nanbeige)
        brain_provider, worker_provider = await self._ensure_local()
        provider = worker_provider or self._ollama
        worker_name = settings.worker_model or WORKER_MODEL

        if provider:
            try:
                async for chunk in provider.stream_chat(
                    messages, model=worker_name, **kwargs
                ):
                    yield chunk
                self._record_usage(user_id, "local")
                self._set_routing(
                    worker_name, "mlx", is_local=True,
                    reason=f"eco_on_stream: worker",
                )
                return
            except Exception as exc:
                logger.warning("Local worker stream failed: %s", exc)

        # Fallback
        response = await self._fallback(
            messages, user_id, settings, reason="eco_on_stream_fallback", **kwargs
        )
        yield StreamChunk(
            delta=response.content, model=response.model, done=True,
        )

    async def _stream_hybrid(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        role: str,
        model: str | None,
        **kwargs,
    ):
        """HYBRID streaming: paid brain streaming, local worker → chunk."""
        if role == ROLE_BRAIN:
            brain_name = model or settings.brain_model or PAID_BRAIN_MODEL
            self._record_usage(user_id, "paid")
            provider = "anthropic" if brain_name.startswith("claude-") else "openai"
            self._set_routing(
                brain_name, provider, is_local=False, reason="hybrid_stream: brain",
            )
            async for chunk in self._paid_router.stream_chat(
                messages, model=brain_name, user_id=user_id, **kwargs
            ):
                yield chunk
            return

        # Worker: local streaming
        async for chunk in self._stream_eco_on(
            messages, user_id, settings, ROLE_WORKER, model, **kwargs
        ):
            yield chunk

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

        brain_provider, worker_provider = await self._ensure_local()
        mlx_available = brain_provider is not None
        ollama_available = self._ollama is not None

        mode_labels = {
            MODE_ECO_ON: "ECO ON (Local)",
            MODE_ECO_HYBRID: "ECO HYBRID",
            MODE_ECO_OFF: "ECO OFF (Paid)",
        }

        brain_model = settings.brain_model or (
            BRAIN_MODEL if settings.mode == MODE_ECO_ON else PAID_BRAIN_MODEL
        )
        worker_model = settings.worker_model or (
            WORKER_MODEL if settings.mode != MODE_ECO_OFF else PAID_WORKER_MODEL
        )
        fallback_model = settings.fallback_model or FALLBACK_MODEL

        return {
            "mode": settings.mode,
            "mode_label": mode_labels.get(settings.mode, settings.mode),
            "brain_model": brain_model,
            "worker_model": worker_model,
            "fallback_model": fallback_model,
            "max_workers": settings.max_workers,
            "auto_fallback": settings.auto_fallback,
            "mlx_available": mlx_available,
            "ollama_available": ollama_available,
            "free_providers": list(self._get_free_keys().keys()),
            "usage": self.get_usage(user_id),
        }
