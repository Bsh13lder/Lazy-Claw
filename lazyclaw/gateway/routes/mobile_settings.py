"""Mobile-friendly settings endpoints.

Provides compact GET/POST endpoints for the LazyClaw mobile app to read and
write the two most common settings:

  GET  /api/settings/eco           → {mode, modes, brain, worker, fallback}
  POST /api/settings/eco           → {mode: "HYBRID"} → same shape back
  GET  /api/settings/notifications → {channel}
  POST /api/settings/notifications → {channel: "app"} → {channel} back

These wrap the existing /api/eco/settings and /api/permissions/* machinery
without duplicating any logic. The web app continues to use the richer
/api/eco/settings shape; the mobile app uses this slim summary view.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.llm.eco_router import VALID_MODES
from lazyclaw.llm.eco_settings import get_eco_settings, update_eco_settings
from lazyclaw.llm.model_registry import MODE_MODELS
from lazyclaw.notifications.channel import (
    VALID_CHANNELS,
    get_notification_channel,
    set_notification_channel,
)

router = APIRouter(prefix="/api/settings", tags=["mobile-settings"])

# Canonical mode list in display order (matches SOUL.md + CLAUDE.md)
_MODES_ORDERED: list[str] = ["HYBRID", "FULL", "CLAUDE", "MINIMAX"]


def _build_eco_summary(eco: dict) -> dict:
    """Return the compact mobile-friendly ECO shape from full settings dict."""
    mode = eco.get("mode", "hybrid")
    mode_key = mode.lower()
    defaults = MODE_MODELS.get(mode_key, MODE_MODELS["hybrid"])

    # Per-mode overrides take priority over generic overrides, then defaults
    brain = (
        eco.get(f"{mode_key}_brain_model")
        or eco.get("brain_model")
        or defaults["brain"]
    )
    worker = (
        eco.get(f"{mode_key}_worker_model")
        or eco.get("worker_model")
        or defaults["worker"]
    )
    fallback = (
        eco.get(f"{mode_key}_fallback_model")
        or eco.get("fallback_model")
        or defaults["fallback"]
    )

    return {
        "mode": mode.upper(),
        "modes": _MODES_ORDERED,
        "brain": brain,
        "worker": worker,
        "fallback": fallback,
    }


class SetEcoModeRequest(BaseModel):
    mode: str  # case-insensitive; validated against VALID_MODES


@router.get("/eco")
async def get_eco_summary(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Mobile ECO summary — compact shape for mobile Settings screen.

    Returns:
        {
          "success": true,
          "data": {
            "mode": "HYBRID",
            "modes": ["HYBRID", "FULL", "CLAUDE", "MINIMAX"],
            "brain": "claude-sonnet-4-6",
            "worker": "lazyclaw-e2b",
            "fallback": "claude-haiku-4-5-20251001"
          }
        }
    """
    eco = await get_eco_settings(config, user.id)
    return {"success": True, "data": _build_eco_summary(eco)}


@router.post("/eco")
async def set_eco_mode(
    body: SetEcoModeRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Set ECO mode for mobile Settings screen.

    Accepts: {"mode": "HYBRID"}  (case-insensitive)
    Rejects:  {"mode": "INVALID"} with 200 success=false + error message

    Returns the same compact summary shape as GET /api/settings/eco.
    """
    raw = body.mode.strip().lower()
    if raw not in VALID_MODES:
        return {
            "success": False,
            "error": (
                f"Invalid mode: {body.mode!r}. "
                f"Valid modes: {', '.join(sorted(VALID_MODES))}"
            ),
        }

    try:
        new_eco = await update_eco_settings(config, user.id, {"mode": raw})
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    return {"success": True, "data": _build_eco_summary(new_eco)}


# ── Notification channel (telegram | app | both) ─────────────────────────


class SetNotificationChannelRequest(BaseModel):
    channel: str  # validated against VALID_CHANNELS


@router.get("/notifications")
async def get_notification_channel_route(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Read the per-user notification delivery channel.

    Returns:
        {"success": true, "data": {"channel": "telegram"}}

    ``channel`` ∈ {"telegram", "app", "both"} (default "telegram").
    """
    channel = await get_notification_channel(config, user.id)
    return {"success": True, "data": {"channel": channel}}


@router.post("/notifications")
async def set_notification_channel_route(
    body: SetNotificationChannelRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Set the notification delivery channel.

    Accepts: {"channel": "app"}  (telegram | app | both, case-insensitive)
    Rejects invalid values with 200 success=false + error message.
    """
    try:
        channel = await set_notification_channel(config, user.id, body.channel)
    except ValueError:
        return {
            "success": False,
            "error": (
                f"Invalid channel: {body.channel!r}. "
                f"Valid channels: {', '.join(VALID_CHANNELS)}"
            ),
        }
    return {"success": True, "data": {"channel": channel}}
