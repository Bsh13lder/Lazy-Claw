"""Awake mode API — keep the macOS host running lid-closed.

Thin gateway over ``lazyclaw.host.awake_client`` + ``lazyclaw.settings.general``.
Used by the Web UI status badge and the Power tab in Settings.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.host import awake_client
from lazyclaw.host.awake_client import AwakeBridgeUnavailable
from lazyclaw.settings.general import get_general_settings, update_general_settings

_config = load_config()

router = APIRouter(prefix="/api/awake", tags=["awake"])


class ToggleBody(BaseModel):
    enabled: bool


class AwakeSettingsBody(BaseModel):
    enabled: bool | None = None
    daily_wake_enabled: bool | None = None
    daily_wake_time: str | None = None


@router.get("/status")
async def get_status(user: User = Depends(get_current_user)):
    settings = await get_general_settings(_config, user.id)
    awake_cfg = settings.get("awake", {})

    bridge_up = awake_client.is_configured()
    live: dict = {}
    if bridge_up:
        try:
            live = await awake_client.status()
        except AwakeBridgeUnavailable:
            bridge_up = False

    return {
        "success": True,
        "data": {
            "bridge_installed": awake_client._MARKER.exists(),
            "bridge_reachable": bridge_up,
            "caffeinate_running": live.get("caffeinate_running", False),
            "on_ac_power": live.get("on_ac_power"),
            "battery_percent": live.get("battery_percent"),
            "daily_wake": live.get("daily_wake"),
            "scheduled_wakes": live.get("scheduled_wakes", []),
            "settings": awake_cfg,
        },
    }


@router.post("/toggle")
async def toggle(body: ToggleBody, user: User = Depends(get_current_user)):
    try:
        if body.enabled:
            await awake_client.turn_on()
        else:
            await awake_client.turn_off()
    except AwakeBridgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    await update_general_settings(_config, user.id, {"awake": {"enabled": body.enabled}})
    return {"success": True, "enabled": body.enabled}


@router.patch("/settings")
async def patch_settings(body: AwakeSettingsBody, user: User = Depends(get_current_user)):
    updates: dict = {}
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.daily_wake_enabled is not None:
        updates["daily_wake_enabled"] = body.daily_wake_enabled
        if body.daily_wake_enabled and body.daily_wake_time:
            try:
                await awake_client.set_daily_wake(body.daily_wake_time, True)
            except AwakeBridgeUnavailable:
                pass  # settings saved; reconcile will apply on next tick
        elif not body.daily_wake_enabled:
            try:
                await awake_client.set_daily_wake("07:00", False)
            except AwakeBridgeUnavailable:
                pass
    if body.daily_wake_time is not None:
        updates["daily_wake_time"] = body.daily_wake_time

    if updates:
        await update_general_settings(_config, user.id, {"awake": updates})

    settings = await get_general_settings(_config, user.id)
    return {"success": True, "data": settings.get("awake", {})}
