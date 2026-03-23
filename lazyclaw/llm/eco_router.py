"""ECO Router — Smart token routing between free, local, and paid AI.

Four modes:
- LOCAL:  Ollama only, $0 always. Brain + specialist local models.
- ECO:    Free API providers only, $0 always. Cascades all providers.
- HYBRID: Free workers + paid brain (Haiku). Haiku fallback.
- FULL:   Always paid. Maximum quality.

Sits between agent.py and llm/router.py. For free calls, uses
lazyclaw.llm.free_providers directly (no mcp-freeride dependency).
For local calls, uses OllamaProvider directly via OpenAI-compatible API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session
from lazyclaw.llm.free_providers import (
    PRIORITY_ORDER,
    PROVIDER_DEFS,
    FreeProviderResult,
    cascade_chat,
    chat as free_chat,
    discover_providers,
    stream_chat as free_stream_chat,
)
from lazyclaw.llm.model_registry import (
    BRAIN_MODEL,
    SPECIALIST_MODEL,
    RoutingResult,
    get_model,
)
from lazyclaw.llm.providers.base import LLMMessage, LLMResponse, StreamChunk
from lazyclaw.llm.rate_limiter import RateLimiter
from lazyclaw.llm.router import LLMRouter

logger = logging.getLogger(__name__)

# Task types for classification
TASK_FREE = "free"
TASK_PAID = "paid"

# Complexity tiers for model routing (NanoClaw-inspired)
COMPLEXITY_SIMPLE = "simple"
COMPLEXITY_STANDARD = "standard"
COMPLEXITY_COMPLEX = "complex"

# Keywords that suggest tasks suitable for free providers (low-quality OK)
# Only truly disposable tasks — NOT conversations, NOT user-facing responses
_FREE_PATTERNS = re.compile(
    r"\b(categorize this|classify this|detect duplicates|"
    r"suggest deadline|prioritize these)\b",
    re.IGNORECASE,
)

# Keywords that MUST use paid (tools, complex, user-facing conversation)
_PAID_PATTERNS = re.compile(
    r"\b(search|browse|find online|look up|web search|remember|"
    r"save.*memory|create.*skill|write.*code|generate.*code|"
    r"run.*command|execute|take.*screenshot|check.*file|read.*file|"
    r"write.*file|schedule|set.*reminder|cron|browser|open.*page|"
    r"click|navigate|vault|credential|api.*key)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EcoSettings:
    """User's ECO mode configuration."""

    mode: str = "full"  # eco, hybrid, full, local
    show_badges: bool = True
    monthly_paid_budget: float = 0.0  # 0 = unlimited
    locked_provider: str | None = None  # Lock to specific free provider
    allowed_providers: list[str] | None = None  # Custom provider pool
    task_overrides: dict[str, str] | None = None  # task_type → provider/model
    brain_model: str | None = None       # Override brain model (None = default)
    specialist_model: str | None = None  # Override specialist model (None = default)
    free_providers: list[str] | None = None  # Configured free providers
    preferred_free_model: str | None = None  # Preferred free model override


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

    task_overrides = eco.get("task_overrides")
    if task_overrides and not isinstance(task_overrides, dict):
        task_overrides = None

    free_providers = eco.get("free_providers")
    if free_providers and not isinstance(free_providers, list):
        free_providers = None

    return EcoSettings(
        mode=eco.get("mode", "full"),
        show_badges=eco.get("show_badges", True),
        monthly_paid_budget=float(eco.get("monthly_paid_budget", 0)),
        locked_provider=eco.get("locked_provider"),
        allowed_providers=allowed,
        task_overrides=task_overrides,
        brain_model=eco.get("brain_model"),
        specialist_model=eco.get("specialist_model"),
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


# Keywords that suggest complex analysis tasks (worth the best model)
_COMPLEX_PATTERNS = re.compile(
    r"\b(analyze|compare|plan|debug|research|investigate|evaluate|"
    r"architect|design|refactor|review|audit|benchmark|optimize|"
    r"explain.*code|trace.*bug|root.*cause)\b",
    re.IGNORECASE,
)

# Simple action keywords (reused from team lead's filter)
_SIMPLE_ACTION_PATTERN = re.compile(
    r"\b(search|browse|find|create|write|run|schedule|calculate|"
    r"check|read|remind|list|show|fetch|tell|what|where|is there|"
    r"open|look|see|get)\b",
    re.IGNORECASE,
)


def classify_complexity(message: str, has_tools: bool) -> str:
    """Fast heuristic for model tier routing. No LLM call needed.

    Inspired by NanoClaw's select_model(text_length, item_count).
    """
    if _COMPLEX_PATTERNS.search(message):
        return COMPLEXITY_COMPLEX

    # Simple tool tasks (list, check, show, read) → cheap model
    if has_tools and _SIMPLE_ACTION_PATTERN.search(message) and len(message) < 120:
        return COMPLEXITY_SIMPLE

    if not has_tools and len(message) < 100:
        lower = message.lower().strip()
        if len(lower) < 40 or not _SIMPLE_ACTION_PATTERN.search(lower):
            return COMPLEXITY_SIMPLE

    return COMPLEXITY_STANDARD


def classify_task(message: str, has_tools: bool) -> str:
    """Classify whether a message needs free or paid AI.

    Conservative — GPT-5 is the default for everything user-facing.
    Free providers only for specific low-quality-OK tasks (categorize, dedup, etc).
    """
    if has_tools:
        return TASK_PAID
    if _PAID_PATTERNS.search(message):
        return TASK_PAID
    if _FREE_PATTERNS.search(message):
        return TASK_FREE
    # Default: GPT-5 for all conversations, greetings, questions, everything
    return TASK_PAID


def _extract_user_message(messages: list[LLMMessage]) -> str:
    """Extract the latest user message from the conversation."""
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return ""


# ── Haiku model for hybrid brain/fallback ──────────────────────────────

HYBRID_BRAIN_MODEL = "claude-haiku-4-5-20251001"


class EcoRouter:
    """Routes requests between local (Ollama), free, and paid providers.

    Usage:
        eco = EcoRouter(config, paid_router)
        response = await eco.chat(messages, user_id, tools=[...])
        routing = eco.last_routing  # RoutingResult for attribution
    """

    def __init__(self, config: Config, paid_router: LLMRouter) -> None:
        self._config = config
        self._paid_router = paid_router
        self._rate_limiter = RateLimiter()
        self._usage: dict[str, dict] = {}  # user_id → {"free": N, "paid": N, "local": N}

        # Cached free provider API keys (lazy init)
        self._free_keys: dict[str, str] | None = None

        # Ollama provider (lazy init, protected by lock)
        self._ollama = None  # OllamaProvider | None
        self._ollama_checked = False  # True after first health check attempt
        self._ollama_lock = asyncio.Lock()

        # Routing attribution — set after every chat() call
        self.last_routing: RoutingResult | None = None

        # Per-model stats for TUI routing panel
        self._routing_stats: dict[str, dict] = {}  # model → {calls, tokens_in, tokens_out}

    # ── Free provider keys ─────────────────────────────────────────────

    def _get_free_keys(self) -> dict[str, str]:
        """Discover and cache free provider API keys from env."""
        if self._free_keys is None:
            self._free_keys = discover_providers()
            if self._free_keys:
                names = ", ".join(self._free_keys.keys())
                logger.info("Free providers available: %s", names)
            else:
                logger.info("No free provider API keys found in environment")
        return self._free_keys

    def _get_provider_order(self, settings: EcoSettings) -> list[str]:
        """Get ordered list of free providers to try, respecting user settings."""
        keys = self._get_free_keys()
        if not keys:
            return []

        # If user locked to a specific provider, use only that
        if settings.locked_provider and settings.locked_provider in keys:
            return [settings.locked_provider]

        # If user specified a provider list, respect it
        if settings.free_providers:
            return [p for p in settings.free_providers if p in keys]

        # Default: priority order filtered to configured providers
        return [p for p in PRIORITY_ORDER if p in keys]

    def refresh_free_keys(self) -> None:
        """Re-scan env vars for free provider keys (e.g. after /eco add)."""
        self._free_keys = None

    # ── Ollama provider ───────────────────────────────────────────────

    async def _ensure_ollama(self):
        """Lazy-init Ollama provider with health check. Returns None if down."""
        if self._ollama is not None:
            return self._ollama
        if self._ollama_checked:
            return None  # Already checked and failed

        async with self._ollama_lock:
            # Double-check after acquiring lock (another coroutine may have initialized)
            if self._ollama is not None:
                return self._ollama
            if self._ollama_checked:
                return None

            from lazyclaw.llm.providers.ollama_provider import OllamaProvider

            provider = OllamaProvider()
            healthy = await provider.health_check()
            self._ollama_checked = True

            if not healthy:
                logger.warning("Ollama not available at localhost:11434 — local mode disabled")
                return None

            self._ollama = provider
            logger.info("Ollama connected — local models available")
            return provider

    def reset_ollama_check(self) -> None:
        """Reset Ollama health check cache (e.g. after user restarts Ollama).

        Safe to call from any context — the lock protects re-init.
        """
        self._ollama = None
        self._ollama_checked = False

    # ── Message conversion ────────────────────────────────────────────

    def _convert_to_dicts(self, messages: list[LLMMessage]) -> list[dict]:
        """Convert LLMMessage list to OpenAI-format dicts for free providers.

        Free APIs don't support tool roles, so we convert tool context
        into plain user/assistant messages to preserve conversation flow.
        """
        result = []
        for msg in messages:
            if msg.role == "tool":
                # Preserve tool results as user context
                result.append({
                    "role": "user",
                    "content": f"[Tool result: {msg.content}]",
                })
            elif msg.tool_calls:
                # Convert tool-calling assistant message to plain text
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
        """Track usage stats per user. Route: 'free', 'paid', or 'local'."""
        if user_id not in self._usage:
            self._usage[user_id] = {"free": 0, "paid": 0, "local": 0}
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

    # ── Free provider helper ──────────────────────────────────────────

    async def _try_free(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        **kwargs,
    ) -> LLMResponse | None:
        """Try free providers in priority order. Returns None if all fail.

        Uses the rate limiter to skip providers that are at capacity,
        then cascades through remaining providers.
        """
        keys = self._get_free_keys()
        if not keys:
            return None

        order = self._get_provider_order(settings)
        if not order:
            return None

        # Filter to providers with rate limit capacity
        available = [
            p for p in order
            if self._rate_limiter.has_capacity(p)
        ]
        if not available:
            # All rate-limited — find shortest wait
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
        self.last_routing = RoutingResult(
            model=result.model,
            provider=result.provider,
            is_local=False,
            reason=f"free: {result.provider}/{result.model}",
        )
        self._record_routing_stats(result.model, result.usage)

        content = result.content
        if settings.show_badges:
            content = f"[ECO {result.provider}/{result.model}] {content}"

        return LLMResponse(
            content=content,
            model=result.model,
            usage={
                **result.usage,
                "provider": result.provider,
                "eco_mode": settings.mode,
            },
        )

    async def _try_free_stream(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
    ) -> AsyncIteratorOrNone:
        """Try streaming from free providers. Returns None if no provider available."""
        keys = self._get_free_keys()
        if not keys:
            return None

        order = self._get_provider_order(settings)
        available = [p for p in order if self._rate_limiter.has_capacity(p)]
        if not available:
            return None

        dict_messages = self._convert_to_dicts(messages)

        # Try each provider until one works
        for provider_name in available:
            api_key = keys.get(provider_name)
            if not api_key:
                continue

            model = None
            if settings.preferred_free_model:
                pdef = PROVIDER_DEFS.get(provider_name)
                if pdef and settings.preferred_free_model in pdef.free_models:
                    model = settings.preferred_free_model

            try:
                # Return a generator wrapper that handles attribution
                return _FreeStreamWrapper(
                    provider_name=provider_name,
                    api_key=api_key,
                    messages=dict_messages,
                    model=model,
                    eco_router=self,
                    user_id=user_id,
                    settings=settings,
                )
            except Exception as exc:
                logger.warning("Free stream %s failed: %s", provider_name, exc)
                continue

        return None

    # ── Main chat router ──────────────────────────────────────────────

    async def chat(
        self,
        messages: list[LLMMessage],
        user_id: str,
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Route chat to local, free, or paid based on ECO mode settings."""
        settings = await _load_eco_settings(self._config, user_id)
        has_tools = bool(kwargs.get("tools"))

        if settings.mode == "local":
            return await self._route_local(messages, user_id, settings, has_tools, **kwargs)

        if settings.mode == "full":
            return await self._route_full(messages, user_id, model, has_tools, **kwargs)

        if settings.mode == "eco":
            return await self._route_eco(messages, user_id, settings, has_tools, **kwargs)

        if settings.mode == "hybrid":
            return await self._route_hybrid(messages, user_id, settings, model, has_tools, **kwargs)

        # Unknown mode, fall back to paid
        self._record_usage(user_id, "paid")
        self.last_routing = RoutingResult(
            model=model or self._config.brain_model,
            provider="openai", is_local=False, reason="unknown_mode -> paid",
        )
        return await self._paid_router.chat(messages, model=model, user_id=user_id, **kwargs)

    # ── FULL mode ─────────────────────────────────────────────────────

    async def _route_full(
        self,
        messages: list[LLMMessage],
        user_id: str,
        model: str | None,
        has_tools: bool,
        **kwargs,
    ) -> LLMResponse:
        """FULL mode: always paid, complexity-based model selection."""
        self._record_usage(user_id, "paid")

        effective_model = model
        if effective_model is None:
            # Complexity routing: gpt-5-mini for simple, gpt-5 for complex
            user_message = _extract_user_message(messages)
            complexity = classify_complexity(user_message, has_tools)
            if complexity == COMPLEXITY_COMPLEX:
                effective_model = self._config.brain_model or self._config.brain_model
            else:
                effective_model = self._config.worker_model or self._config.brain_model
            logger.info("FULL mode: %s -> %s", complexity, effective_model)

        # Infer provider for attribution
        provider = "openai"
        if effective_model and effective_model.startswith("claude-"):
            provider = "anthropic"

        self.last_routing = RoutingResult(
            model=effective_model or self._config.brain_model,
            provider=provider,
            is_local=False,
            reason=f"full: {effective_model}",
        )

        response = await self._paid_router.chat(
            messages, model=effective_model, user_id=user_id, **kwargs
        )
        self._record_routing_stats(
            effective_model or self._config.brain_model,
            response.usage,
        )
        return response

    # ── LOCAL mode ────────────────────────────────────────────────────

    async def _route_local(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        has_tools: bool,
        **kwargs,
    ) -> LLMResponse:
        """LOCAL mode: Ollama only, $0 always. Brain for simple, specialist for tools."""
        from lazyclaw.llm.providers.ollama_provider import OllamaUnavailableError

        ollama = await self._ensure_ollama()
        if not ollama:
            logger.warning("LOCAL: Ollama unavailable, falling back to paid")
            # Fall back to paid so the agent can still help (e.g. answer questions,
            # help user install Ollama, use ollama_install skill)
            self.last_routing = RoutingResult(
                model=self._config.worker_model,
                provider="openai",
                is_local=False,
                reason="local_fallback: ollama_unavailable",
            )
            self._record_usage(user_id, "paid")
            return await self._paid_router.chat(
                messages, model=self._config.worker_model, user_id=user_id, **kwargs
            )

        user_message = _extract_user_message(messages)
        complexity = classify_complexity(user_message, has_tools)

        # User-configurable models (fall back to defaults from model_registry)
        brain = settings.brain_model or BRAIN_MODEL
        specialist = settings.specialist_model or SPECIALIST_MODEL

        # Determine model order: brain first for non-tool tasks, specialist for tools
        if has_tools:
            models_to_try = [specialist, brain]
        elif complexity == COMPLEXITY_SIMPLE:
            models_to_try = [brain]
        else:
            # Standard/complex without tools: try brain first (faster), specialist if fails
            models_to_try = [brain, specialist]

        for local_model in models_to_try:
            reason = f"local: {complexity} -> {local_model.split('/')[-1]}"
            self.last_routing = RoutingResult(
                model=local_model, provider="ollama", is_local=True, reason=reason,
            )

            local_kwargs = dict(kwargs)
            if local_model == brain:
                local_kwargs.pop("tools", None)
                local_kwargs.pop("tool_choice", None)

            try:
                self._record_usage(user_id, "local")
                response = await ollama.chat(messages, model=local_model, **local_kwargs)
                self._record_routing_stats(local_model, response.usage)
                return response
            except OllamaUnavailableError as exc:
                logger.warning("LOCAL: Model %s failed: %s", local_model, exc)
                # Don't reset ollama check on model errors (400/404) —
                # only reset on connection errors so we don't spam reconnects
                if "Cannot connect" in str(exc):
                    self.reset_ollama_check()
                continue  # Try next model

        # All local models failed — fall back to paid
        logger.warning("LOCAL: All local models failed, falling back to paid")
        self.last_routing = RoutingResult(
            model=self._config.worker_model,
            provider="openai",
            is_local=False,
            reason="local_fallback: all_models_failed",
        )
        self._record_usage(user_id, "paid")
        return await self._paid_router.chat(
            messages, model=self._config.worker_model, user_id=user_id, **kwargs
        )

    # ── HYBRID mode ───────────────────────────────────────────────────

    async def _route_hybrid(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        model: str | None,
        has_tools: bool,
        **kwargs,
    ) -> LLMResponse:
        """HYBRID mode: free workers + paid brain (Haiku). Haiku fallback.

        Brain (Haiku, paid, cheap, always reliable) handles decisions.
        Workers (free providers) handle execution — specialists, tools, etc.
        If free rate-limited → fallback to Haiku (NOT Sonnet — keep it cheap).
        """
        # Try free providers first for all tasks
        free_response = await self._try_free(messages, user_id, settings, **kwargs)
        if free_response is not None:
            return free_response

        # Free failed/rate-limited → fall back to Haiku (cheap paid)
        fallback_model = model or HYBRID_BRAIN_MODEL
        logger.info("HYBRID: free providers exhausted, falling back to %s", fallback_model)

        provider = "anthropic" if fallback_model.startswith("claude-") else "openai"
        self.last_routing = RoutingResult(
            model=fallback_model, provider=provider, is_local=False,
            reason=f"hybrid_fallback: free_exhausted -> {fallback_model}",
        )
        self._record_usage(user_id, "paid")
        response = await self._paid_router.chat(
            messages, model=fallback_model, user_id=user_id, **kwargs
        )
        self._record_routing_stats(fallback_model, response.usage)

        if settings.show_badges and response.content:
            response = LLMResponse(
                content=f"[PAID {response.model}] {response.content}",
                model=response.model,
                usage=response.usage,
                tool_calls=response.tool_calls,
            )
        return response

    # ── ECO mode ──────────────────────────────────────────────────────

    async def _route_eco(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        has_tools: bool,
        **kwargs,
    ) -> LLMResponse:
        """ECO mode: free providers only, never paid. Cascades all, waits if rate-limited."""
        keys = self._get_free_keys()
        if not keys:
            self.last_routing = RoutingResult(
                model="none", provider="free", is_local=False, reason="eco: no_providers",
            )
            return LLMResponse(
                content=(
                    "ECO mode requires at least one free provider.\n\n"
                    "Run /eco setup to configure free API keys, or switch to paid: /eco full"
                ),
                model="none",
            )

        # Try with patience — ECO never pays, so wait for rate limits
        max_wait_rounds = 6  # Max ~3 minutes total
        for attempt in range(max_wait_rounds):
            free_response = await self._try_free(messages, user_id, settings, **kwargs)
            if free_response is not None:
                return free_response

            if attempt < max_wait_rounds - 1:
                # Find the shortest wait across all providers
                order = self._get_provider_order(settings)
                min_wait = min(
                    (self._rate_limiter.wait_seconds(p) for p in order),
                    default=30,
                )
                wait = max(5, min(min_wait + 2, 30))  # Clamp 5-30s
                logger.info(
                    "ECO: all providers rate-limited, waiting %.0fs (attempt %d/%d)",
                    wait, attempt + 1, max_wait_rounds,
                )
                await asyncio.sleep(wait)

        self.last_routing = RoutingResult(
            model="none", provider="free", is_local=False,
            reason="eco: all_providers_rate_limited",
        )
        configured = ", ".join(keys.keys())
        return LLMResponse(
            content=(
                f"All free providers are rate-limited ({configured}).\n"
                "Please try again in a few minutes, or switch to HYBRID mode: /eco hybrid"
            ),
            model="none",
        )

    # ── Streaming ─────────────────────────────────────────────────────

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        user_id: str,
        model: str | None = None,
        **kwargs,
    ):
        """Stream chat responses.

        Free providers support true SSE streaming. Local falls back to
        non-streaming single chunk. Paid uses paid provider streaming.
        """
        settings = await _load_eco_settings(self._config, user_id)

        # LOCAL mode: non-streaming single chunk
        if settings.mode == "local":
            response = await self._route_local(
                messages, user_id, settings, bool(kwargs.get("tools")), **kwargs
            )
            yield StreamChunk(
                delta=response.content,
                tool_calls=response.tool_calls,
                usage=response.usage,
                model=response.model,
                done=True,
            )
            return

        # FULL mode: true streaming via paid provider
        if settings.mode == "full":
            # Complexity routing (same logic as non-streaming _route_full)
            effective_model = model
            if effective_model is None:
                user_message = _extract_user_message(messages)
                _has_tools = bool(kwargs.get("tools"))
                complexity = classify_complexity(user_message, _has_tools)
                if complexity == COMPLEXITY_COMPLEX:
                    effective_model = self._config.brain_model or self._config.brain_model
                else:
                    effective_model = self._config.worker_model or self._config.brain_model
                logger.info("FULL stream: %s -> %s", complexity, effective_model)
            provider = "openai"
            if effective_model and effective_model.startswith("claude-"):
                provider = "anthropic"
            self.last_routing = RoutingResult(
                model=effective_model or self._config.brain_model,
                provider=provider, is_local=False,
                reason=f"full_stream: {effective_model}",
            )
            self._record_usage(user_id, "paid")
            async for chunk in self._paid_router.stream_chat(
                messages, model=effective_model, user_id=user_id, **kwargs
            ):
                yield chunk
            return

        # ECO mode: stream from free providers, no paid fallback
        if settings.mode == "eco":
            async for chunk in self._stream_free_or_error(
                messages, user_id, settings,
                error_msg="Free providers unavailable. Try again shortly, or: /eco hybrid",
            ):
                yield chunk
            return

        # HYBRID mode: try free streaming first, then paid Haiku streaming
        if settings.mode == "hybrid":
            stream_wrapper = await self._try_free_stream(messages, user_id, settings)
            if stream_wrapper is not None:
                async for chunk in stream_wrapper:
                    yield chunk
                return
            logger.info("HYBRID stream: free providers exhausted, falling back to Haiku")

        # Paid streaming fallback (hybrid after free fails, or unknown mode)
        fallback_model = model or HYBRID_BRAIN_MODEL
        self._record_usage(user_id, "paid")
        provider = "anthropic" if fallback_model.startswith("claude-") else "openai"
        self.last_routing = RoutingResult(
            model=fallback_model, provider=provider, is_local=False,
            reason="paid_stream_fallback",
        )

        badge_prefix = ""
        if settings.show_badges and settings.mode == "hybrid":
            badge_prefix = "[PAID] "

        first_chunk = True
        async for chunk in self._paid_router.stream_chat(
            messages, model=fallback_model, user_id=user_id, **kwargs
        ):
            if first_chunk and badge_prefix and chunk.delta:
                yield StreamChunk(
                    delta=badge_prefix + chunk.delta,
                    tool_calls=chunk.tool_calls,
                    usage=chunk.usage,
                    model=chunk.model,
                    done=chunk.done,
                )
                first_chunk = False
            else:
                yield chunk

    async def _stream_free_or_error(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        error_msg: str,
    ):
        """Stream from free providers, or yield error chunk."""
        stream_wrapper = await self._try_free_stream(messages, user_id, settings)
        if stream_wrapper is not None:
            async for chunk in stream_wrapper:
                yield chunk
            return

        yield StreamChunk(delta=error_msg, model="none", done=True)

    # ── Stats ─────────────────────────────────────────────────────────

    def get_usage(self, user_id: str) -> dict:
        """Get usage stats for a user."""
        stats = self._usage.get(user_id, {"free": 0, "paid": 0, "local": 0})
        local = stats.get("local", 0)
        free = stats.get("free", 0)
        paid = stats.get("paid", 0)
        total = local + free + paid
        return {
            "local_count": local,
            "free_count": free,
            "paid_count": paid,
            "total": total,
            "local_percentage": round(local / total * 100, 1) if total > 0 else 0,
            "free_percentage": round(free / total * 100, 1) if total > 0 else 0,
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
        """Get status of all free providers (configured or not)."""
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


# ── Free stream wrapper ───────────────────────────────────────────────

# Type alias for optional async iterator
AsyncIteratorOrNone = object  # Can't type async iterator union cleanly


class _FreeStreamWrapper:
    """Wraps free_providers.stream_chat to handle attribution and badges."""

    def __init__(
        self,
        provider_name: str,
        api_key: str,
        messages: list[dict],
        model: str | None,
        eco_router: EcoRouter,
        user_id: str,
        settings: EcoSettings,
    ) -> None:
        self._provider_name = provider_name
        self._api_key = api_key
        self._messages = messages
        self._model = model
        self._eco_router = eco_router
        self._user_id = user_id
        self._settings = settings

    def __aiter__(self):
        return self._stream()

    async def _stream(self):
        first = True
        try:
            async for chunk in free_stream_chat(
                self._provider_name,
                self._api_key,
                self._messages,
                self._model,
            ):
                if first:
                    self._eco_router._rate_limiter.record_request(self._provider_name)
                    self._eco_router._record_usage(self._user_id, "free")
                    self._eco_router.last_routing = RoutingResult(
                        model=chunk.model or self._model or "unknown",
                        provider=self._provider_name,
                        is_local=False,
                        reason=f"free_stream: {self._provider_name}",
                    )
                    first = False

                    badge_prefix = ""
                    if self._settings.show_badges:
                        badge_prefix = f"[ECO {self._provider_name}] "

                    if badge_prefix and chunk.delta:
                        yield StreamChunk(
                            delta=badge_prefix + chunk.delta,
                            model=chunk.model,
                            done=chunk.done,
                        )
                        continue

                yield StreamChunk(
                    delta=chunk.delta,
                    model=chunk.model,
                    done=chunk.done,
                )
        except Exception as exc:
            logger.warning("Free stream %s error: %s", self._provider_name, exc)
            yield StreamChunk(
                delta=f"[Stream error: {self._provider_name}]",
                model="none",
                done=True,
            )
