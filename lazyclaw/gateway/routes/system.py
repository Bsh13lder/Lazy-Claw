"""System info + general per-user settings.

Endpoints:
  GET  /api/settings/general   — current user's generic prefs (search, UI)
  PATCH /api/settings/general  — update generic prefs
  GET  /api/system/about       — version, uptime, providers, quota, db path
"""

from __future__ import annotations

import logging
import platform
import sys
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lazyclaw import __version__ as LAZYCLAW_VERSION
from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.settings.general import (
    get_general_settings,
    update_general_settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

_STARTED_AT = time.time()


class UpdateGeneralRequest(BaseModel):
    search_provider: str | None = None
    show_cost_badges: bool | None = None
    agent_mode: str | None = None  # ADR-0005: chat | ask | plan | auto


@router.get("/api/settings/general")
async def get_general(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    data = await get_general_settings(config, user.id)
    return {"success": True, "data": data}


@router.patch("/api/settings/general")
async def patch_general(
    body: UpdateGeneralRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"success": False, "error": "No fields to update"}
    try:
        new_settings = await update_general_settings(config, user.id, updates)
        return {"success": True, "data": new_settings}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


@router.get("/api/system/about")
async def get_about(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """System diagnostics — safe to show in the UI."""
    # Lazy imports keep this route cheap at import time.
    import os

    from lazyclaw.llm.eco_settings import get_eco_settings
    from lazyclaw.llm.free_providers import discover_providers
    from lazyclaw.skills.builtin.web_search import (
        _BRAVE_MONTHLY_LIMIT,
        get_search_usage,
    )

    eco = await get_eco_settings(config, user.id)
    general = await get_general_settings(config, user.id)
    usage = get_search_usage()
    # Authoritative API-key presence — checks vault FIRST (chat-set keys
    # take precedence and don't require restart), then falls back to env.
    # The UI shows ✓ when either source has it.
    has_brave_env = bool(os.environ.get("BRAVE_KEY", "").strip())
    has_brave_vault = False
    try:
        from lazyclaw.crypto.vault import get_credential
        stored = await get_credential(config, user.id, "brave_key")
        has_brave_vault = bool(stored)
    except Exception as exc:
        logger.debug("vault check for brave_key failed: %s", exc)
    search_keys = {
        "brave": has_brave_env or has_brave_vault,
        "brave_source": (
            "vault" if has_brave_vault
            else ("env" if has_brave_env else "none")
        ),
    }

    try:
        free_providers = sorted(discover_providers().keys())
    except Exception as exc:
        logger.debug("discover_providers failed: %s", exc)
        free_providers = []

    mcp_count = 0
    try:
        from lazyclaw.mcp.manager import list_servers

        servers = await list_servers(config, user.id)
        mcp_count = len(servers)
    except Exception as exc:
        logger.debug("list_servers failed: %s", exc)

    data = {
        "version": LAZYCLAW_VERSION,
        "started_at": _STARTED_AT,
        "uptime_seconds": max(0, int(time.time() - _STARTED_AT)),
        "python_version": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "db_path": str(config.database_dir),
        "eco_mode": eco.get("mode", "hybrid"),
        "search_provider": general.get("search_provider", "auto"),
        "search_quota": {
            "brave_used": usage.brave_count,
            "brave_limit": _BRAVE_MONTHLY_LIMIT,
            "scraper_used": usage.scraper_count,
            "reset_month": usage.reset_month,
        },
        "search_keys": search_keys,
        "free_providers": free_providers,
        "telegram_configured": bool(config.telegram_bot_token),
        "mcp_server_count": mcp_count,
    }
    return {"success": True, "data": data}
