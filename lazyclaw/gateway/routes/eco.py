"""ECO mode REST API — settings and usage dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.llm.eco_settings import get_eco_settings, update_eco_settings

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

    free_info = get_provider_info()

    # Build paid providers from env / config
    paid_providers = [
        {
            "name": "anthropic",
            "display_name": "Anthropic (Claude)",
            "configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "is_paid": True,
            "env_key": "ANTHROPIC_API_KEY",
        },
        {
            "name": "openai",
            "display_name": "OpenAI (GPT)",
            "configured": bool(os.environ.get("OPENAI_API_KEY")),
            "is_paid": True,
            "env_key": "OPENAI_API_KEY",
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
