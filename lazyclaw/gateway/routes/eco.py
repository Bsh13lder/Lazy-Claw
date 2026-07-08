"""ECO mode REST API — settings and usage dashboard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.llm.eco_settings import get_eco_settings, update_eco_settings
from lazyclaw.llm.providers._claude_token import (
    claude_auth_status,
    clear_claude_oauth_token,
    write_claude_oauth_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/eco", tags=["eco"])

# Shared EcoRouter instance — set by cli.py at startup
_eco_router_instance = None


def set_eco_deps(eco_router_ref) -> None:
    """Called by cli.py to inject the live EcoRouter for usage tracking."""
    global _eco_router_instance
    _eco_router_instance = eco_router_ref


class UpdateEcoRequest(BaseModel):
    mode: str | None = None
    show_badges: bool | None = None
    monthly_paid_budget: float | None = None
    locked_provider: str | None = None
    allowed_providers: list[str] | None = None
    free_providers: list[str] | None = None
    preferred_free_model: str | None = None
    # Per-mode model overrides
    hybrid_brain_model: str | None = None
    hybrid_worker_model: str | None = None
    hybrid_fallback_model: str | None = None
    full_brain_model: str | None = None
    full_worker_model: str | None = None
    full_fallback_model: str | None = None
    claude_brain_model: str | None = None
    claude_worker_model: str | None = None
    claude_fallback_model: str | None = None


@router.get("/settings")
async def get_settings(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Get user's ECO mode settings."""
    settings = await get_eco_settings(config, user.id)
    return {"success": True, "data": settings}


@router.patch("/settings")
async def update_settings(
    body: UpdateEcoRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Update ECO mode settings."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"success": False, "error": "No fields to update"}
    try:
        new_settings = await update_eco_settings(config, user.id, updates)
        return {"success": True, "data": new_settings}
    except ValueError as e:
        return {"success": False, "error": str(e)}


class ClaudeTokenRequest(BaseModel):
    token: str


@router.get("/claude-auth")
async def get_claude_auth(user: User = Depends(get_current_user)):
    """Cheap auth-status for the CLAUDE-mode subscription (Settings badge)."""
    return {"success": True, "data": claude_auth_status()}


@router.post("/claude-token")
async def set_claude_token(
    body: ClaudeTokenRequest,
    user: User = Depends(get_current_user),
):
    """Store a `claude setup-token` for CLAUDE mode + live-verify it.

    An empty token clears the stored value (auth falls back to the
    `claude /login` credentials file). A non-empty token is format-checked,
    stored, then verified with a one-token ping. On a genuine auth failure we
    discard it so a bad paste can't shadow a working login credential; on a
    transient/timeout we keep it (the user can retry a real message).
    """
    token = (body.token or "").strip()
    if not token:
        clear_claude_oauth_token()
        return {"success": True, "data": {"authenticated": False, "detail": "Token cleared."}}

    if not token.startswith("sk-ant-"):
        logger.warning(
            "claude-token save REJECTED bad prefix: %r… (len=%d)", token[:10], len(token)
        )
        return {
            "success": False,
            "error": (
                "That doesn't look like a Claude token — it should start with "
                "'sk-ant-oat'. Run `claude setup-token` (or use the Login button)."
            ),
        }

    write_claude_oauth_token(token)
    logger.warning(
        "claude-token save: stored token prefix=%r len=%d — verifying…",
        token[:12], len(token),
    )

    # Live-verify. Imported lazily so a stale image without the helper still boots.
    from lazyclaw.llm.providers.claude_sdk_provider import verify_claude_sdk_auth

    ok, detail = await verify_claude_sdk_auth()
    logger.warning("claude-token verify result: ok=%s detail=%s", ok, detail)
    if not ok and "authentication failed" in detail.lower():
        clear_claude_oauth_token()
        return {"success": False, "error": detail}
    return {"success": True, "data": {"authenticated": ok, "detail": detail}}


class ClaudeLoginCompleteRequest(BaseModel):
    login_id: str
    code: str


@router.post("/claude-login/start")
async def claude_login_start(user: User = Depends(get_current_user)):
    """One-click login step 1: spawn `claude setup-token` (pty) → return the
    OAuth URL for the browser to open + a login_id to resume with the code."""
    import asyncio

    from lazyclaw.llm.providers._claude_login import start_login

    logger.warning("claude-login START requested")
    try:
        data = await asyncio.to_thread(start_login)
        logger.warning("claude-login START ok — auth URL captured, login_id issued")
        return {"success": True, "data": data}
    except Exception as exc:  # noqa: BLE001 — surface a clean message
        logger.warning("claude-login START failed: %s", exc)
        return {"success": False, "error": str(exc)}


@router.post("/claude-login/complete")
async def claude_login_complete(
    body: ClaudeLoginCompleteRequest,
    user: User = Depends(get_current_user),
):
    """One-click login step 2: feed the pasted code into the pty, capture +
    store the token, then live-verify it."""
    import asyncio

    from lazyclaw.llm.providers._claude_login import complete_login
    from lazyclaw.llm.providers.claude_sdk_provider import verify_claude_sdk_auth

    logger.warning("claude-login COMPLETE requested (code len=%d)", len((body.code or "").strip()))
    try:
        await asyncio.to_thread(complete_login, body.login_id, body.code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("claude-login COMPLETE failed: %s", exc)
        return {"success": False, "error": str(exc)}

    ok, detail = await verify_claude_sdk_auth()
    logger.warning("claude-login COMPLETE verify: ok=%s detail=%s", ok, detail)
    if not ok and "authentication failed" in detail.lower():
        clear_claude_oauth_token()
        return {"success": False, "error": detail}
    return {"success": True, "data": {"authenticated": ok, "detail": detail}}


@router.get("/usage")
async def get_usage(user: User = Depends(get_current_user)):
    """Get token usage stats for the current user."""
    if _eco_router_instance is None:
        return {
            "success": True,
            "data": {
                "free_count": 0,
                "paid_count": 0,
                "total": 0,
                "free_percentage": 0,
                "message": "Usage tracking active when eco_router is initialized",
            },
        }

    raw = _eco_router_instance.get_usage(user.id)
    # Map: local + free are both "free" from the user's cost perspective
    free = raw.get("local_count", 0) + raw.get("free_count", 0)
    paid = raw.get("paid_count", 0)
    total = raw.get("total", 0)
    free_pct = round(free / total * 100, 1) if total > 0 else 0
    return {
        "success": True,
        "data": {
            "free_count": free,
            "paid_count": paid,
            "total": total,
            "free_percentage": free_pct,
        },
    }


@router.get("/costs")
async def get_costs(user: User = Depends(get_current_user)):
    """Get per-model cost breakdown (mirrors TUI cost panel)."""
    if _eco_router_instance is None:
        return {
            "success": True,
            "data": {
                "models": {},
                "total_cost": 0.0,
                "total_calls": 0,
                "local_pct": 0,
            },
        }

    stats = _eco_router_instance.get_routing_stats()
    return {"success": True, "data": stats}


@router.get("/rates")
async def get_rates(user: User = Depends(get_current_user)):
    """Get all model token rates (cost per 1K tokens)."""
    from lazyclaw.llm.pricing import MODEL_COSTS

    rates = [
        {
            "model": model,
            "input_per_1k": costs["input"],
            "output_per_1k": costs["output"],
            "is_free": costs["input"] == 0 and costs["output"] == 0,
        }
        for model, costs in MODEL_COSTS.items()
    ]
    return {"success": True, "data": {"rates": rates}}


@router.post("/rates/refresh")
async def refresh_rates(user: User = Depends(get_current_user)):
    """Refresh model rates from provider pricing pages."""
    from lazyclaw.llm.pricing import refresh_rates as do_refresh

    updated = await do_refresh()
    return {"success": True, "data": {"updated_models": updated}}


@router.get("/rate-limits")
async def get_rate_limits(user: User = Depends(get_current_user)):
    """Get current rate limit status for all free providers."""
    from lazyclaw.llm.rate_limiter import KNOWN_LIMITS

    status = {}
    for name, limits in KNOWN_LIMITS.items():
        status[name] = {
            "requests_per_minute": limits.requests_per_minute,
            "requests_per_day": limits.requests_per_day,
            "tokens_per_minute": limits.tokens_per_minute,
        }
    return {"success": True, "data": status}


@router.get("/providers")
async def list_providers(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """List all AI providers (paid + free) and their configuration status."""
    import os
    from lazyclaw.llm.free_providers import get_provider_info
    from lazyclaw.crypto.vault import get_credential

    free_info = get_provider_info()

    # Check both env AND vault for paid provider keys
    async def paid_configured(env_key: str) -> bool:
        if os.environ.get(env_key):
            return True
        vault_val = await get_credential(config, user.id, env_key)
        return bool(vault_val and vault_val.strip())

    # Build paid providers from env / config
    paid_providers = [
        {
            "name": "anthropic",
            "display_name": "Anthropic (Claude)",
            "configured": await paid_configured("ANTHROPIC_API_KEY"),
            "is_paid": True,
            "env_key": "ANTHROPIC_API_KEY",
        },
        {
            "name": "openai",
            "display_name": "OpenAI (GPT)",
            "configured": await paid_configured("OPENAI_API_KEY"),
            "is_paid": True,
            "env_key": "OPENAI_API_KEY",
        },
        {
            "name": "minimax",
            "display_name": "MiniMax (Token Plan)",
            "configured": await paid_configured("MINIMAX_API_KEY"),
            "is_paid": True,
            "env_key": "MINIMAX_API_KEY",
        },
    ]

    # Enrich free providers with extra fields
    all_providers = paid_providers + [
        {**p, "is_paid": False, "display_name": p["name"].title(), "env_key": p.get("env_key", "")}
        for p in free_info
    ]

    configured = [p["name"] for p in all_providers if p["configured"]]
    return {
        "success": True,
        "data": {
            "configured": configured,
            "all_providers": all_providers,
        },
    }


@router.get("/models")
async def list_models(user: User = Depends(get_current_user)):
    """List all available AI models from the catalog."""
    from lazyclaw.llm.model_registry import MODEL_CATALOG, MODE_MODELS

    models = [
        {
            "id": p.name,
            "display_name": p.display_name,
            "provider": p.provider,
            "is_local": p.is_local,
            "role": p.role,
            "tool_calling": p.tool_calling,
            "optimized": p.provider == "anthropic",
        }
        for p in MODEL_CATALOG.values()
    ]
    return {
        "success": True,
        "data": {
            "models": models,
            "mode_defaults": {
                mode: dict(roles) for mode, roles in MODE_MODELS.items()
            },
        },
    }
