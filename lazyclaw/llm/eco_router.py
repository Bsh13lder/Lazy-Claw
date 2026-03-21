"""ECO Router — Smart token routing between free, local, and paid AI.

Four modes:
- LOCAL:  Ollama only, $0 always. Brain + specialist local models.
- ECO:    Free API providers only, $0 always. Waits if rate-limited.
- HYBRID: Local first, paid fallback. Auto-decides per task complexity.
- FULL:   Always paid. Maximum quality.

Sits between agent.py and llm/router.py. For free calls, uses
mcp-freeride's FreeRideRouter directly (as library, not MCP protocol).
For local calls, uses OllamaProvider directly via OpenAI-compatible API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import derive_server_key, decrypt_field
from lazyclaw.db.connection import db_session
from lazyclaw.llm.model_registry import (
    BRAIN_MODEL,
    SPECIALIST_MODEL,
    RoutingResult,
    get_model,
)
from lazyclaw.llm.providers.base import LLMMessage, LLMResponse
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

    return EcoSettings(
        mode=eco.get("mode", "full"),
        show_badges=eco.get("show_badges", True),
        monthly_paid_budget=float(eco.get("monthly_paid_budget", 0)),
        locked_provider=eco.get("locked_provider"),
        allowed_providers=allowed,
        task_overrides=task_overrides,
        brain_model=eco.get("brain_model"),
        specialist_model=eco.get("specialist_model"),
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


class EcoRouter:
    """Routes requests between local (Ollama), free (mcp-freeride), and paid.

    Usage:
        eco = EcoRouter(config, paid_router)
        response = await eco.chat(messages, user_id, tools=[...])
        routing = eco.last_routing  # RoutingResult for attribution
    """

    def __init__(self, config: Config, paid_router: LLMRouter) -> None:
        self._config = config
        self._paid_router = paid_router
        self._free_router = None  # Lazy init
        self._free_router_unavailable = False  # Cache import failure
        self._rate_limiter = RateLimiter()
        self._usage: dict[str, dict] = {}  # user_id → {"free": N, "paid": N, "local": N}

        # Ollama provider (lazy init, protected by lock)
        self._ollama = None  # OllamaProvider | None
        self._ollama_checked = False  # True after first health check attempt
        self._ollama_lock = asyncio.Lock()

        # Routing attribution — set after every chat() call
        self.last_routing: RoutingResult | None = None

        # Per-model stats for TUI routing panel
        self._routing_stats: dict[str, dict] = {}  # model → {calls, tokens_in, tokens_out}

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

    # ── Free router (mcp-freeride) ────────────────────────────────────

    def _get_free_router(self):
        """Lazy-init the free router from mcp-freeride."""
        if self._free_router is not None:
            return self._free_router
        if self._free_router_unavailable:
            return None
        try:
            from mcp_freeride.config import load_config as load_freeride_config
            from mcp_freeride.router import FreeRideRouter

            freeride_config = load_freeride_config()
            self._free_router = FreeRideRouter(freeride_config)
            return self._free_router
        except ImportError:
            self._free_router_unavailable = True
            logger.warning("mcp-freeride not installed, ECO mode unavailable")
            return None

    async def _ensure_free_router(self):
        """Lazy-init free router + load apihunter providers (async)."""
        if self._free_router is not None:
            return self._free_router

        # Use sync init first
        router = self._get_free_router()
        if router is None:
            return None

        # Then async-load apihunter providers
        try:
            count = await router.load_apihunter_providers_async()
            if count > 0:
                logger.info("Loaded %d providers from apihunter", count)
        except Exception:
            logger.debug("Failed to load apihunter providers", exc_info=True)

        # Also refresh Ollama models
        try:
            await router.refresh_ollama()
        except Exception:
            logger.debug("Failed to refresh Ollama models", exc_info=True)

        return router

    # ── Message conversion ────────────────────────────────────────────

    def _convert_to_dicts(self, messages: list[LLMMessage]) -> list[dict]:
        """Convert LLMMessage list to OpenAI-format dicts for free router.

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
            model=model or self._config.default_model,
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
            # GPT-5 for all tasks — better tool decisions, reliable execution
            effective_model = self._config.smart_model
            logger.info("FULL mode -> %s", effective_model)

        # Infer provider for attribution
        provider = "openai"
        if effective_model and effective_model.startswith("claude-"):
            provider = "anthropic"

        self.last_routing = RoutingResult(
            model=effective_model or self._config.default_model,
            provider=provider,
            is_local=False,
            reason=f"full: {effective_model}",
        )

        response = await self._paid_router.chat(
            messages, model=effective_model, user_id=user_id, **kwargs
        )
        self._record_routing_stats(
            effective_model or self._config.default_model,
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
                model=self._config.fast_model,
                provider="openai",
                is_local=False,
                reason="local_fallback: ollama_unavailable",
            )
            self._record_usage(user_id, "paid")
            return await self._paid_router.chat(
                messages, model=self._config.fast_model, user_id=user_id, **kwargs
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

        for model in models_to_try:
            reason = f"local: {complexity} -> {model.split('/')[-1]}"
            self.last_routing = RoutingResult(
                model=model, provider="ollama", is_local=True, reason=reason,
            )

            local_kwargs = dict(kwargs)
            if model == brain:
                local_kwargs.pop("tools", None)
                local_kwargs.pop("tool_choice", None)

            try:
                self._record_usage(user_id, "local")
                response = await ollama.chat(messages, model=model, **local_kwargs)
                self._record_routing_stats(model, response.usage)
                return response
            except OllamaUnavailableError as exc:
                logger.warning("LOCAL: Model %s failed: %s", model, exc)
                # Don't reset ollama check on model errors (400/404) —
                # only reset on connection errors so we don't spam reconnects
                if "Cannot connect" in str(exc):
                    self.reset_ollama_check()
                continue  # Try next model

        # All local models failed — fall back to paid
        logger.warning("LOCAL: All local models failed, falling back to paid")
        self.last_routing = RoutingResult(
            model=self._config.fast_model,
            provider="openai",
            is_local=False,
            reason="local_fallback: all_models_failed",
        )
        self._record_usage(user_id, "paid")
        return await self._paid_router.chat(
            messages, model=self._config.fast_model, user_id=user_id, **kwargs
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
        """HYBRID mode: try local first, then free APIs, then paid."""
        from lazyclaw.llm.providers.ollama_provider import OllamaUnavailableError

        user_message = _extract_user_message(messages)
        complexity = classify_complexity(user_message, has_tools)

        # User-configurable models
        brain = settings.brain_model or BRAIN_MODEL
        specialist = settings.specialist_model or SPECIALIST_MODEL

        # Priority 1: Simple chat without tools → brain model (local, free)
        if complexity == COMPLEXITY_SIMPLE and not has_tools:
            ollama = await self._ensure_ollama()
            if ollama:
                self.last_routing = RoutingResult(
                    model=brain, provider="ollama", is_local=True,
                    reason="hybrid: simple_chat -> brain",
                )
                self._record_usage(user_id, "local")
                kwargs_no_tools = dict(kwargs)
                kwargs_no_tools.pop("tools", None)
                kwargs_no_tools.pop("tool_choice", None)
                try:
                    response = await ollama.chat(messages, model=brain, **kwargs_no_tools)
                    self._record_routing_stats(brain, response.usage)
                    return response
                except OllamaUnavailableError as exc:
                    logger.info("HYBRID: Local brain failed: %s", exc)
                    if "Cannot connect" in str(exc):
                        self.reset_ollama_check()

        # Priority 2: Standard/simple+tools → specialist model (local, free)
        if complexity in (COMPLEXITY_SIMPLE, COMPLEXITY_STANDARD):
            ollama = await self._ensure_ollama()
            if ollama:
                self.last_routing = RoutingResult(
                    model=specialist, provider="ollama", is_local=True,
                    reason=f"hybrid: {complexity} -> specialist",
                )
                self._record_usage(user_id, "local")
                try:
                    response = await ollama.chat(messages, model=specialist, **kwargs)
                    self._record_routing_stats(specialist, response.usage)
                    return response
                except OllamaUnavailableError as exc:
                    logger.info("HYBRID: Local specialist failed: %s", exc)
                    if "Cannot connect" in str(exc):
                        self.reset_ollama_check()

        # Priority 3: Check task overrides for free API routing
        task_type = classify_task(user_message, has_tools)
        if settings.task_overrides:
            for pattern, _override_provider in settings.task_overrides.items():
                if pattern.lower() in user_message.lower():
                    task_type = TASK_FREE
                    break

        if task_type == TASK_FREE:
            free_router = await self._ensure_free_router()
            if free_router:
                model_hint = None
                if settings.locked_provider:
                    model_hint = settings.locked_provider + "/"

                try:
                    dict_messages = self._convert_to_dicts(messages)
                    result = await free_router.chat(dict_messages, model_hint)
                    provider = result.get("provider", "free")
                    self._rate_limiter.record_request(provider)
                    self._record_usage(user_id, "free")
                    self.last_routing = RoutingResult(
                        model=result.get("model", "free"),
                        provider=provider,
                        is_local=False,
                        reason="hybrid: free_api",
                    )

                    content = result["content"]
                    if settings.show_badges:
                        badge = f"[ECO {result.get('provider', '?')}/{result.get('model', '?')}] "
                        content = badge + content

                    return LLMResponse(
                        content=content,
                        model=result.get("model", "free"),
                        usage={"provider": provider, "eco_mode": "hybrid_free"},
                    )
                except Exception:
                    logger.info("HYBRID: Free failed, falling back to paid")

        # Priority 4: Paid path (complex tasks or all local/free failed)
        effective_model = model or self._config.fast_model
        provider = "openai"
        if effective_model.startswith("claude-"):
            provider = "anthropic"

        self.last_routing = RoutingResult(
            model=effective_model, provider=provider, is_local=False,
            reason=f"hybrid: {complexity} -> paid",
        )
        self._record_usage(user_id, "paid")
        response = await self._paid_router.chat(
            messages, model=effective_model, user_id=user_id, **kwargs
        )
        self._record_routing_stats(effective_model, response.usage)

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
        """ECO mode: free only, wait if rate-limited, never paid."""
        free_router = await self._ensure_free_router()
        if not free_router:
            self.last_routing = RoutingResult(
                model="none", provider="free", is_local=False, reason="eco: no_providers",
            )
            return LLMResponse(
                content=(
                    "ECO mode is enabled but no free AI providers are configured.\n\n"
                    "Quick setup (pick any — all are free):\n"
                    "\u2022 Groq (fastest): https://console.groq.com \u2192 Get API Key \u2192 add GROQ_API_KEY to .env\n"
                    "\u2022 Gemini: https://aistudio.google.com/apikey \u2192 add GEMINI_API_KEY to .env\n"
                    "\u2022 OpenRouter: https://openrouter.ai/keys \u2192 add OPENROUTER_API_KEY to .env\n"
                    "\u2022 HuggingFace: https://huggingface.co/settings/tokens \u2192 add HF_API_KEY to .env\n\n"
                    "Then restart LazyClaw. Or switch to paid mode: /eco full"
                ),
                model="none",
            )

        # Build model hint from locked_provider or task override
        model_hint = None
        if settings.locked_provider:
            model_hint = settings.locked_provider + "/"

        # Wait for rate limit capacity (ECO = patient, never paid)
        max_wait_rounds = 6  # Max ~3 minutes total
        for attempt in range(max_wait_rounds):
            try:
                dict_messages = self._convert_to_dicts(messages)
                result = await asyncio.wait_for(
                    free_router.chat(dict_messages, model_hint),
                    timeout=15,
                )
                provider = result.get("provider", "free")
                self._rate_limiter.record_request(provider)
                self._record_usage(user_id, "free")
                self.last_routing = RoutingResult(
                    model=result.get("model", "free"),
                    provider=provider,
                    is_local=False,
                    reason="eco: free_only",
                )

                content = result["content"]
                if settings.show_badges:
                    badge = f"[ECO {result.get('provider', '?')}/{result.get('model', '?')}] "
                    content = badge + content

                return LLMResponse(
                    content=content,
                    model=result.get("model", "free"),
                    usage={"provider": provider, "eco_mode": "eco"},
                )
            except Exception as exc:
                logger.warning("ECO provider error: %s", exc)
                if "All" in str(exc) and attempt < max_wait_rounds - 1:
                    wait = 30
                    logger.info("ECO: All providers busy, waiting %ds (attempt %d)", wait, attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                raise

        self.last_routing = RoutingResult(
            model="none", provider="free", is_local=False, reason="eco: all_rate_limited",
        )
        return LLMResponse(
            content="All free AI providers are currently rate-limited. "
            "Please try again in a few minutes, or switch to HYBRID mode.",
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

        Paid path supports true streaming. Local/ECO/hybrid free paths
        fall back to non-streaming and yield a single chunk.
        """
        from lazyclaw.llm.providers.base import StreamChunk
        from lazyclaw.llm.providers.ollama_provider import OllamaUnavailableError

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
            # Set routing attribution for full mode
            effective_model = model
            if effective_model is None:
                effective_model = self._config.smart_model
            provider = "openai"
            if effective_model and effective_model.startswith("claude-"):
                provider = "anthropic"
            self.last_routing = RoutingResult(
                model=effective_model or self._config.default_model,
                provider=provider, is_local=False,
                reason=f"full_stream: {effective_model}",
            )
            self._record_usage(user_id, "paid")
            async for chunk in self._paid_router.stream_chat(
                messages, model=effective_model, user_id=user_id, **kwargs
            ):
                yield chunk
            return

        # HYBRID mode: try local first, then free, then paid streaming
        if settings.mode == "hybrid":
            has_tools = bool(kwargs.get("tools"))
            user_message = _extract_user_message(messages)
            complexity = classify_complexity(user_message, has_tools)
            brain = settings.brain_model or BRAIN_MODEL
            specialist = settings.specialist_model or SPECIALIST_MODEL

            # Try local brain for simple chat
            if complexity == COMPLEXITY_SIMPLE and not has_tools:
                ollama = await self._ensure_ollama()
                if ollama:
                    try:
                        kwargs_no_tools = dict(kwargs)
                        kwargs_no_tools.pop("tools", None)
                        kwargs_no_tools.pop("tool_choice", None)
                        response = await ollama.chat(messages, model=brain, **kwargs_no_tools)
                        self.last_routing = RoutingResult(
                            model=brain, provider="ollama", is_local=True,
                            reason="hybrid_stream: simple -> brain",
                        )
                        self._record_usage(user_id, "local")
                        self._record_routing_stats(brain, response.usage)
                        yield StreamChunk(
                            delta=response.content,
                            tool_calls=response.tool_calls,
                            usage=response.usage,
                            model=response.model,
                            done=True,
                        )
                        return
                    except OllamaUnavailableError:
                        logger.info("HYBRID stream: brain failed, falling through")

            # Try local specialist for standard tasks
            if complexity in (COMPLEXITY_SIMPLE, COMPLEXITY_STANDARD):
                ollama = await self._ensure_ollama()
                if ollama:
                    try:
                        response = await ollama.chat(messages, model=specialist, **kwargs)
                        self.last_routing = RoutingResult(
                            model=specialist, provider="ollama", is_local=True,
                            reason=f"hybrid_stream: {complexity} -> specialist",
                        )
                        self._record_usage(user_id, "local")
                        self._record_routing_stats(specialist, response.usage)
                        yield StreamChunk(
                            delta=response.content,
                            tool_calls=response.tool_calls,
                            usage=response.usage,
                            model=response.model,
                            done=True,
                        )
                        return
                    except OllamaUnavailableError:
                        logger.info("HYBRID stream: specialist failed, falling through")

        # ECO/HYBRID free path
        if settings.mode in ("eco", "hybrid"):
            has_tools = bool(kwargs.get("tools"))
            user_message = _extract_user_message(messages)
            use_free = settings.mode == "eco" or classify_task(user_message, has_tools) == TASK_FREE

            if use_free:
                free_router = await self._ensure_free_router()
                if free_router:
                    try:
                        dict_messages = self._convert_to_dicts(messages)
                        result = await asyncio.wait_for(
                            free_router.chat(dict_messages, None),
                            timeout=15,
                        )
                        provider = result.get("provider", "free")
                        self._rate_limiter.record_request(provider)
                        self._record_usage(user_id, "free")
                        self.last_routing = RoutingResult(
                            model=result.get("model", "free"),
                            provider=provider,
                            is_local=False,
                            reason=f"{settings.mode}_stream: free",
                        )

                        content = result["content"]
                        if settings.show_badges:
                            badge = f"[ECO {provider}/{result.get('model', '?')}] "
                            content = badge + content

                        yield StreamChunk(
                            delta=content,
                            model=result.get("model", "free"),
                            usage={"provider": provider, "eco_mode": settings.mode},
                            done=True,
                        )
                        return
                    except Exception as exc:
                        logger.warning("ECO streaming failed: %s", exc, exc_info=True)
                        if settings.mode == "eco":
                            yield StreamChunk(
                                delta="Free AI providers unavailable. Try: set eco hybrid",
                                model="none",
                                done=True,
                            )
                            return
                        logger.info("HYBRID: Free failed, falling back to paid streaming")

        # Paid streaming fallback
        self._record_usage(user_id, "paid")
        effective_model = model or self._config.fast_model
        provider = "openai"
        if effective_model.startswith("claude-"):
            provider = "anthropic"
        self.last_routing = RoutingResult(
            model=effective_model, provider=provider, is_local=False,
            reason="paid_stream_fallback",
        )

        badge_prefix = ""
        if settings.show_badges and settings.mode == "hybrid":
            badge_prefix = "[PAID] "

        first_chunk = True
        async for chunk in self._paid_router.stream_chat(
            messages, model=model, user_id=user_id, **kwargs
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
