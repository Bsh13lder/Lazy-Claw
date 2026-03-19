"""ECO Router — Smart token routing between free and paid AI.

Three modes:
- ECO:    Free only, $0 always. Waits if rate-limited. No tool calling.
- HYBRID: Agent auto-decides per task. Simple → free, complex → paid.
- FULL:   Always paid. Current behavior, maximum quality.

Sits between agent.py and llm/router.py. For free calls, uses
mcp-freeride's FreeRideRouter directly (as library, not MCP protocol).
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

    mode: str = "full"  # eco, hybrid, full
    show_badges: bool = True
    monthly_paid_budget: float = 0.0  # 0 = unlimited
    locked_provider: str | None = None  # Lock to specific free provider
    allowed_providers: list[str] | None = None  # Custom provider pool
    task_overrides: dict[str, str] | None = None  # task_type → provider/model


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
    r"check|read|remind|list|show|fetch)\b",
    re.IGNORECASE,
)


def classify_complexity(message: str, has_tools: bool) -> str:
    """Fast heuristic for model tier routing. No LLM call needed.

    Inspired by NanoClaw's select_model(text_length, item_count).
    """
    if _COMPLEX_PATTERNS.search(message):
        return COMPLEXITY_COMPLEX

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


class EcoRouter:
    """Routes requests between free (mcp-freeride) and paid (LLM router).

    Usage:
        eco = EcoRouter(config, paid_router)
        response = await eco.chat(messages, user_id, tools=[...])
    """

    def __init__(self, config: Config, paid_router: LLMRouter) -> None:
        self._config = config
        self._paid_router = paid_router
        self._free_router = None  # Lazy init
        self._free_router_unavailable = False  # Cache import failure
        self._rate_limiter = RateLimiter()
        self._usage: dict[str, dict] = {}  # user_id → {"free": count, "paid": count}

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

    def _record_usage(self, user_id: str, route: str) -> None:
        """Track usage stats per user."""
        if user_id not in self._usage:
            self._usage[user_id] = {"free": 0, "paid": 0}
        self._usage[user_id][route] += 1

    async def chat(
        self,
        messages: list[LLMMessage],
        user_id: str,
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Route chat to free or paid based on ECO mode settings."""
        settings = await _load_eco_settings(self._config, user_id)
        has_tools = bool(kwargs.get("tools"))

        if settings.mode == "full":
            self._record_usage(user_id, "paid")
            # Complexity-based model routing when no explicit model override
            effective_model = model
            if effective_model is None:
                user_message = ""
                for msg in reversed(messages):
                    if msg.role == "user":
                        user_message = msg.content
                        break
                complexity = classify_complexity(user_message, has_tools)
                if complexity == COMPLEXITY_COMPLEX:
                    effective_model = self._config.smart_model
                    logger.info("Complexity: COMPLEX → %s", effective_model)
                else:
                    # SIMPLE + STANDARD both use fast model (5x cheaper)
                    # GPT-5-mini handles tool selection, chat, and browser tasks fine
                    effective_model = self._config.fast_model
                    logger.info("Complexity: %s → %s", complexity, effective_model)
            return await self._paid_router.chat(messages, model=effective_model, user_id=user_id, **kwargs)

        if settings.mode == "eco":
            return await self._route_eco(messages, user_id, settings, has_tools, **kwargs)

        if settings.mode == "hybrid":
            return await self._route_hybrid(messages, user_id, settings, model, has_tools, **kwargs)

        # Unknown mode, fall back to paid
        self._record_usage(user_id, "paid")
        return await self._paid_router.chat(messages, model=model, user_id=user_id, **kwargs)

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
            return LLMResponse(
                content=(
                    "ECO mode is enabled but no free AI providers are configured.\n\n"
                    "Quick setup (pick any — all are free):\n"
                    "• Groq (fastest): https://console.groq.com → Get API Key → add GROQ_API_KEY to .env\n"
                    "• Gemini: https://aistudio.google.com/apikey → add GEMINI_API_KEY to .env\n"
                    "• OpenRouter: https://openrouter.ai/keys → add OPENROUTER_API_KEY to .env\n"
                    "• HuggingFace: https://huggingface.co/settings/tokens → add HF_API_KEY to .env\n\n"
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

        return LLMResponse(
            content="All free AI providers are currently rate-limited. "
            "Please try again in a few minutes, or switch to HYBRID mode.",
            model="none",
        )

    async def _route_hybrid(
        self,
        messages: list[LLMMessage],
        user_id: str,
        settings: EcoSettings,
        model: str | None,
        has_tools: bool,
        **kwargs,
    ) -> LLMResponse:
        """HYBRID mode: auto-decide free vs paid per task."""
        # Get the user's latest message for classification
        user_message = ""
        for msg in reversed(messages):
            if msg.role == "user":
                user_message = msg.content
                break

        task_type = classify_task(user_message, has_tools)

        # Check task overrides from user settings
        if settings.task_overrides:
            for pattern, override_provider in settings.task_overrides.items():
                if pattern.lower() in user_message.lower():
                    task_type = TASK_FREE
                    break

        # If task needs tools and we classified as paid → use paid
        # If task is simple → try free first
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

        # Paid path (with full tool support)
        self._record_usage(user_id, "paid")
        response = await self._paid_router.chat(messages, model=model, user_id=user_id, **kwargs)

        if settings.show_badges and response.content:
            response = LLMResponse(
                content=f"[PAID {response.model}] {response.content}",
                model=response.model,
                usage=response.usage,
                tool_calls=response.tool_calls,
            )
        return response

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        user_id: str,
        model: str | None = None,
        **kwargs,
    ):
        """Stream chat responses. Only paid path supports streaming.

        ECO/hybrid free path falls back to non-streaming and yields a single chunk.
        """
        from lazyclaw.llm.providers.base import StreamChunk

        settings = await _load_eco_settings(self._config, user_id)

        if settings.mode == "full":
            self._record_usage(user_id, "paid")
            async for chunk in self._paid_router.stream_chat(
                messages, model=model, user_id=user_id, **kwargs
            ):
                yield chunk
            return

        # For eco/hybrid, try free first (non-streaming), fall back to paid streaming
        if settings.mode in ("eco", "hybrid"):
            has_tools = bool(kwargs.get("tools"))
            user_message = ""
            for msg in reversed(messages):
                if msg.role == "user":
                    user_message = msg.content
                    break

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

                        content = result["content"]
                        if settings.show_badges:
                            eco_label = "ECO" if settings.mode == "eco" else "ECO"
                            badge = f"[{eco_label} {provider}/{result.get('model', '?')}] "
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
                                delta="Free AI providers unavailable. Try /eco hybrid for fallback.",
                                model="none",
                                done=True,
                            )
                            return
                        logger.info("HYBRID: Free failed, falling back to paid streaming")

        # Paid streaming fallback
        self._record_usage(user_id, "paid")
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

    def get_usage(self, user_id: str) -> dict:
        """Get usage stats for a user."""
        stats = self._usage.get(user_id, {"free": 0, "paid": 0})
        total = stats["free"] + stats["paid"]
        return {
            "free_count": stats["free"],
            "paid_count": stats["paid"],
            "total": total,
            "free_percentage": round(stats["free"] / total * 100, 1) if total > 0 else 0,
        }

    def get_rate_limit_status(self) -> dict:
        """Get current rate limit status for all providers."""
        return self._rate_limiter.get_status()
