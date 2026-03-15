"""ECO mode REST API — settings and usage dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.llm.eco_settings import get_eco_settings, update_eco_settings

router = APIRouter(prefix="/api/eco", tags=["eco"])


class UpdateEcoRequest(BaseModel):
    mode: str | None = None
    show_badges: bool | None = None
    monthly_paid_budget: float | None = None
    locked_provider: str | None = None
    allowed_providers: list[str] | None = None
    task_overrides: dict[str, str] | None = None


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
    updates = body.model_dump(exclude_none=True)
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
    # Get eco_router from app state (set during startup)
    # For now, return basic structure — wired up during integration
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


@router.get("/rate-limits")
async def get_rate_limits():
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
async def list_providers():
    """List available free AI providers and their status."""
    try:
        from mcp_freeride.config import load_config as load_freeride_config
        from mcp_freeride.config import get_configured_providers

        freeride_config = load_freeride_config()
        configured = get_configured_providers(freeride_config)
        return {
            "success": True,
            "data": {
                "configured": configured,
                "available": ["groq", "gemini", "openrouter", "together", "mistral", "huggingface", "ollama"],
            },
        }
    except ImportError:
        return {
            "success": True,
            "data": {
                "configured": [],
                "available": ["groq", "gemini", "openrouter", "together", "mistral", "huggingface", "ollama"],
                "warning": "mcp-freeride not installed",
            },
        }
