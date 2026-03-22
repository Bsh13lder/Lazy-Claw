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
        """Lazy-init free router."""
        if self._free_router is not None:
            return self._free_router

        router = self._get_free_router()
        if router is None:
            return None

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

    # Default Qwen model for OpenRouter free tier
    QWEN_MODEL = "qwen/qwen3-next-80b-a3b-instruct:free"

    async def _route_hybrid(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        model: str | None,
        has_tools: bool,
        **kwargs,
    ) -> LLMResponse:
        """HYBRID mode: Qwen (OpenRouter) first → GPT-5 fallback."""
        # Priority 1: Try Qwen via OpenRouter (free) for everything
        qwen_response = await self._try_qwen(messages, user_id, settings, **kwargs)
        if qwen_response is not None:
            return qwen_response

        # Priority 2: Qwen failed/stuck → fall back to GPT-5 (full, not mini)
        logger.info("HYBRID: Qwen failed, falling back to GPT-5")
        effective_model = model or self._config.brain_model
        provider = "openai"
        if effective_model.startswith("claude-"):
            provider = "anthropic"

        self.last_routing = RoutingResult(
            model=effective_model, provider=provider, is_local=False,
            reason="hybrid: qwen_failed -> gpt5",
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

    async def _try_qwen(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        **kwargs,
    ) -> LLMResponse | None:
        """Try Qwen via OpenRouter directly. Returns None if unavailable/failed.

        Calls the OpenRouter provider directly (not the free router cascade)
        to avoid falling through to Ollama/Groq with an OpenRouter model ID.
        """
        free_router = await self._ensure_free_router()
        if not free_router:
            return None

        # Get the OpenRouter provider directly — don't cascade through all providers
        openrouter = free_router._providers.get("openrouter")
        if not openrouter:
            logger.info("OpenRouter provider not configured in free router")
            return None

        try:
            dict_messages = self._convert_to_dicts(messages)
            result = await asyncio.wait_for(
                openrouter.chat(dict_messages, self.QWEN_MODEL),
                timeout=20,
            )
            provider = result.get("provider", "openrouter")
            self._rate_limiter.record_request(provider)
            self._record_usage(user_id, "free")
            result_model = result.get("model", self.QWEN_MODEL)
            self.last_routing = RoutingResult(
                model=result_model,
                provider=provider,
                is_local=False,
                reason="qwen_openrouter",
            )
            self._record_routing_stats(result_model, None)

            content = result["content"]
            if settings.show_badges:
                badge = f"[ECO {provider}/{result_model}] "
                content = badge + content

            return LLMResponse(
                content=content,
                model=result_model,
                usage={"provider": provider, "eco_mode": "hybrid_free"},
            )
        except Exception as exc:
            logger.warning("Qwen/OpenRouter failed: %s", exc)
            return None

    # ── ECO mode ──────────────────────────────────────────────────────

    async def _route_eco(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        has_tools: bool,
        **kwargs,
    ) -> LLMResponse:
        """ECO mode: Qwen via OpenRouter only, never paid. Waits if rate-limited."""
        free_router = await self._ensure_free_router()
        if not free_router:
            self.last_routing = RoutingResult(
                model="none", provider="free", is_local=False, reason="eco: no_openrouter",
            )
            return LLMResponse(
                content=(
                    "ECO mode requires OpenRouter for Qwen access.\n\n"
                    "Setup: https://openrouter.ai/keys \u2192 add OPENROUTER_API_KEY to .env\n\n"
                    "Then restart LazyClaw. Or switch to paid mode: /eco full"
                ),
                model="none",
            )

        qwen_hint = f"openrouter/{self.QWEN_MODEL}"

        # Wait for rate limit capacity (ECO = patient, never paid)
        max_wait_rounds = 6  # Max ~3 minutes total
        for attempt in range(max_wait_rounds):
            try:
                dict_messages = self._convert_to_dicts(messages)
                result = await asyncio.wait_for(
                    free_router.chat(dict_messages, qwen_hint),
                    timeout=20,
                )
                provider = result.get("provider", "openrouter")
                self._rate_limiter.record_request(provider)
                self._record_usage(user_id, "free")
                result_model = result.get("model", self.QWEN_MODEL)
                self.last_routing = RoutingResult(
                    model=result_model,
                    provider=provider,
                    is_local=False,
                    reason="eco: qwen_only",
                )

                content = result["content"]
                if settings.show_badges:
                    badge = f"[ECO {provider}/{result_model}] "
                    content = badge + content

                return LLMResponse(
                    content=content,
                    model=result_model,
                    usage={"provider": provider, "eco_mode": "eco"},
                )
            except Exception as exc:
                logger.warning("ECO Qwen error: %s", exc)
                if attempt < max_wait_rounds - 1:
                    wait = 30
                    logger.info("ECO: Qwen rate-limited, waiting %ds (attempt %d)", wait, attempt + 1)
                    await asyncio.sleep(wait)
                    continue

        self.last_routing = RoutingResult(
            model="none", provider="openrouter", is_local=False, reason="eco: qwen_rate_limited",
        )
        return LLMResponse(
            content="Qwen is currently rate-limited on OpenRouter. "
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

        # HYBRID mode: try Qwen first, then paid GPT-5 streaming
        if settings.mode == "hybrid":
            qwen_response = await self._try_qwen(messages, user_id, settings, **kwargs)
            if qwen_response is not None:
                yield StreamChunk(
                    delta=qwen_response.content,
                    tool_calls=qwen_response.tool_calls,
                    usage=qwen_response.usage,
                    model=qwen_response.model,
                    done=True,
                )
                return
            logger.info("HYBRID stream: Qwen failed, falling back to paid GPT-5")

        # ECO mode: Qwen only, no paid fallback
        if settings.mode == "eco":
            qwen_response = await self._try_qwen(messages, user_id, settings, **kwargs)
            if qwen_response is not None:
                yield StreamChunk(
                    delta=qwen_response.content,
                    tool_calls=qwen_response.tool_calls,
                    usage=qwen_response.usage,
                    model=qwen_response.model,
                    done=True,
                )
                return
            yield StreamChunk(
                delta="Qwen unavailable on OpenRouter. Try again shortly, or: /eco hybrid",
                model="none",
                done=True,
            )
            return

        # Paid streaming fallback (hybrid after Qwen fails)
        self._record_usage(user_id, "paid")
        if model:
            effective_model = model
        else:
            user_message = _extract_user_message(messages)
            _has_tools = bool(kwargs.get("tools"))
            complexity = classify_complexity(user_message, _has_tools)
            if complexity == COMPLEXITY_COMPLEX:
                effective_model = self._config.brain_model or self._config.brain_model
            else:
                effective_model = self._config.worker_model or self._config.brain_model
            logger.info("HYBRID fallback stream: %s -> %s", complexity, effective_model)
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
