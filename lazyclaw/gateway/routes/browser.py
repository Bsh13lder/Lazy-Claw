"""Browser API routes — site memory + live canvas + checkpoints + takeover."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from lazyclaw.browser import checkpoints, event_bus, site_memory
from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/browser", tags=["browser"])

_config = load_config()


# ── Site memory endpoints ────────────────────────────────────────────────


@router.get("/site-memory")
async def list_site_memories(user: User = Depends(get_current_user)):
    """List all site memories."""
    memories = await site_memory.recall_all(_config, user.id)
    return {"memories": memories}


@router.delete("/site-memory/{memory_id}")
async def delete_site_memory(
    memory_id: str, user: User = Depends(get_current_user)
):
    """Delete a site memory."""
    deleted = await site_memory.forget(_config, user.id, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted"}


@router.delete("/site-memory/domain/{domain}")
async def delete_domain_memories(
    domain: str, user: User = Depends(get_current_user)
):
    """Delete all site memories for a domain."""
    count = await site_memory.forget_domain(_config, user.id, domain)
    return {"deleted": count}


# ── Live browser canvas ──────────────────────────────────────────────────


@router.get("/state")
async def browser_state(user: User = Depends(get_current_user)):
    """Current URL/title + last recent events for initial paint."""
    state = event_bus.latest_state(user.id)
    events = [e.to_frame() for e in event_bus.recent_events(user.id, limit=8)]
    has_thumb = event_bus.get_thumbnail(user.id) is not None
    return {
        "state": state,
        "events": events,
        "has_thumbnail": has_thumb,
    }


@router.get("/frame")
async def browser_frame(user: User = Depends(get_current_user)):
    """Return the latest browser thumbnail (WebP or PNG) or 204."""
    thumb = event_bus.get_thumbnail(user.id)
    if not thumb:
        return Response(status_code=204)
    # WebP if it starts with "RIFF....WEBP"; else PNG.
    media = "image/webp" if thumb[:4] == b"RIFF" and thumb[8:12] == b"WEBP" else "image/png"
    meta = event_bus.get_thumbnail_meta(user.id) or (None, 0.0)
    headers = {"Cache-Control": "no-store"}
    if meta[0]:
        headers["X-Thumbnail-Url"] = meta[0]
    if meta[1]:
        headers["X-Thumbnail-Age-S"] = f"{int(__import__('time').time() - meta[1])}"
    return Response(content=thumb, media_type=media, headers=headers)


@router.post("/frame/refresh")
async def browser_frame_refresh(user: User = Depends(get_current_user)):
    """Force-capture a fresh thumbnail right now from the live browser.

    Cheap to call: one extra Page.captureScreenshot. Used when the user
    expands the canvas or clicks "Refresh now" — guarantees they don't
    see a stale frame from a previous flow.
    """
    try:
        from lazyclaw.skills.builtin.browser_actions.backends import get_cdp_backend
        backend = await get_cdp_backend(user.id)
        try:
            backend.set_user_id(user.id)
        except AttributeError:
            pass
        url = None
        try:
            url = await backend.current_url()
        except Exception:
            pass
        await backend._capture_thumbnail(url, force=True)
        return {"status": "captured", "url": url}
    except Exception as exc:
        logger.warning("Frame refresh failed: %s", exc)
        # Soft fail — UI can keep showing the cached frame
        return {"status": "no_browser", "error": str(exc)}


@router.get("/live-mode")
async def browser_live_mode_status(user: User = Depends(get_current_user)):
    return {
        "active": event_bus.is_live_mode(user.id),
        "remaining_seconds": event_bus.live_mode_remaining(user.id),
    }


@router.post("/live-mode/start")
async def browser_live_mode_start(
    user: User = Depends(get_current_user),
    payload: dict = Body(default={}),
):
    """Enable per-action thumbnail capture for N seconds (default 300)."""
    seconds = float((payload or {}).get("seconds") or event_bus.LIVE_MODE_DEFAULT_SECONDS)
    seconds = max(30.0, min(1800.0, seconds))  # 30s..30min cap
    expiry = event_bus.set_live_mode(user.id, seconds=seconds)
    event_bus.publish(event_bus.BrowserEvent(
        user_id=user.id, kind="alert",
        detail=f"Live mode on for {int(seconds)}s — capturing every step",
        extra={"live_mode": True, "expires_at": expiry},
    ))
    # Best-effort immediate capture so the canvas updates right away.
    try:
        from lazyclaw.skills.builtin.browser_actions.backends import get_cdp_backend
        backend = await get_cdp_backend(user.id)
        try:
            backend.set_user_id(user.id)
        except AttributeError:
            pass
        url = await backend.current_url()
        await backend._capture_thumbnail(url, force=True)
    except Exception:
        pass
    return {"active": True, "expires_at": expiry, "remaining_seconds": seconds}


@router.post("/live-mode/stop")
async def browser_live_mode_stop(user: User = Depends(get_current_user)):
    event_bus.clear_live_mode(user.id)
    event_bus.publish(event_bus.BrowserEvent(
        user_id=user.id, kind="alert",
        detail="Live mode off",
        extra={"live_mode": False},
    ))
    return {"active": False}


# ── Checkpoints (agent pauses for user approval) ─────────────────────────


@router.get("/checkpoint")
async def checkpoint_pending(user: User = Depends(get_current_user)):
    """Return the pending checkpoint, if any."""
    pending = checkpoints.get_pending(user.id)
    if pending is None:
        return {"pending": None}
    return {
        "pending": {
            "name": pending.name,
            "detail": pending.detail,
            "created_at": pending.created_at,
        }
    }


@router.post("/checkpoint/approve")
async def checkpoint_approve(
    user: User = Depends(get_current_user),
    payload: dict = Body(default={}),
):
    """Approve the pending checkpoint, releasing the agent."""
    name = (payload or {}).get("name")
    reason = (payload or {}).get("reason")
    released = checkpoints.approve(user.id, name=name, reason=reason)
    if not released:
        raise HTTPException(status_code=409, detail="No matching pending checkpoint")
    event_bus.publish(event_bus.BrowserEvent(
        user_id=user.id,
        kind="checkpoint",
        target=name,
        detail=f"Approved: {name}" if name else "Checkpoint approved",
        extra={"resolved": "approved"},
    ))
    return {"status": "approved"}


@router.post("/checkpoint/reject")
async def checkpoint_reject(
    user: User = Depends(get_current_user),
    payload: dict = Body(default={}),
):
    """Reject the pending checkpoint with an optional reason."""
    name = (payload or {}).get("name")
    reason = (payload or {}).get("reason") or "Rejected by user"
    released = checkpoints.reject(user.id, name=name, reason=reason)
    if not released:
        raise HTTPException(status_code=409, detail="No matching pending checkpoint")
    event_bus.publish(event_bus.BrowserEvent(
        user_id=user.id,
        kind="checkpoint",
        target=name,
        detail=f"Rejected: {reason}",
        extra={"resolved": "rejected", "reason": reason},
    ))
    return {"status": "rejected"}


# ── Remote takeover (noVNC) — also usable from Telegram ──────────────────


@router.get("/remote-session")
async def remote_session_status(user: User = Depends(get_current_user)):
    """Return the current takeover session URL (if any)."""
    from lazyclaw.browser.remote_takeover import get_active_session, is_remote_capable

    active = get_active_session(user.id)
    return {
        "active": active is not None,
        "url": active.url if active else None,
        "capable": is_remote_capable(),
    }


@router.post("/remote-session/start")
async def remote_session_start(user: User = Depends(get_current_user)):
    """Start a noVNC takeover session. Returns the URL to share."""
    from lazyclaw.browser.remote_takeover import (
        is_remote_capable, is_server_mode,
        start_macos_remote_session, start_remote_session,
    )
    import sys

    if not is_remote_capable():
        raise HTTPException(
            status_code=503,
            detail=(
                "Remote takeover not available on this host. "
                "macOS: enable System Settings → Sharing → Screen Sharing. "
                "Linux: install xvfb + x11vnc + websockify + noVNC."
            ),
        )
    try:
        port = getattr(_config, "cdp_port", 9222)
        profile_dir = str(_config.database_dir / "browser_profiles" / user.id)
        if is_server_mode():
            session = await start_remote_session(
                user_id=user.id, cdp_port=port,
                profile_dir=profile_dir,
                browser_bin=_config.browser_executable,
            )
        elif sys.platform == "darwin":
            session = await start_macos_remote_session(user.id)
        else:
            raise HTTPException(
                status_code=503,
                detail="Remote takeover requires macOS Screen Sharing or Linux server mode.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to start remote session: %s", exc)
        raise HTTPException(status_code=500, detail=f"Takeover failed: {exc}")

    # Broadcast a takeover event so the canvas lights up.
    event_bus.publish(event_bus.BrowserEvent(
        user_id=user.id,
        kind="takeover",
        detail="Remote takeover started — open the link to drive the browser",
        extra={"url": session.url},
    ))
    return {"url": session.url}


@router.post("/remote-session/stop")
async def remote_session_stop(user: User = Depends(get_current_user)):
    """Stop an active takeover session and resume the agent."""
    from lazyclaw.browser.remote_takeover import stop_remote_session

    await stop_remote_session(user.id)
    event_bus.publish(event_bus.BrowserEvent(
        user_id=user.id,
        kind="takeover",
        detail="Remote takeover ended — agent will resume",
        extra={"url": None},
    ))
    return {"status": "stopped"}


# ── Host browser bridge (CDP to user's real Brave) ────────────────────────
#
# Sibling of /remote-session/* but a different mechanism: the agent drives
# the host browser directly over CDP instead of giving the user a VNC link.


@router.get("/host-session")
async def host_session_status(user: User = Depends(get_current_user)):
    """Report current host-bridge mode + whether host Brave is reachable."""
    from lazyclaw.browser import host_bridge
    from lazyclaw.browser.browser_settings import get_browser_settings

    settings = await get_browser_settings(_config, user.id)
    port = getattr(_config, "cdp_port", 9222)
    reachable_ws = await host_bridge.probe_host_cdp(port)
    return {
        "mode": settings.get("use_host_browser", "off"),
        "runtime": "docker" if host_bridge.is_docker_runtime() else "native",
        "reachable": reachable_ws is not None,
        "last_source": settings.get("last_host_cdp_source"),
        "token_set": bool(settings.get("host_cdp_token")),
    }


@router.post("/host-session/start")
async def host_session_start(user: User = Depends(get_current_user)):
    """Enable host-browser mode; return setup command if Brave isn't reachable.

    Response shapes:
      - ``{"status": "connected", "origin": "..."}`` — ready to use
      - ``{"status": "needs_launch", "command": "...", "warning": "..."}``
        user has to paste the shell one-liner and retry
    """
    from lazyclaw.browser import host_bridge
    from lazyclaw.browser.browser_settings import (
        get_browser_settings, update_browser_settings,
    )

    settings = await get_browser_settings(_config, user.id)
    token = settings.get("host_cdp_token") or host_bridge.generate_host_token()
    if token != settings.get("host_cdp_token"):
        await update_browser_settings(_config, user.id, {"host_cdp_token": token})

    await update_browser_settings(_config, user.id, {"use_host_browser": "auto"})

    port = getattr(_config, "cdp_port", 9222)
    ws_url = await host_bridge.probe_host_cdp(port)
    if ws_url:
        event_bus.publish(event_bus.BrowserEvent(
            user_id=user.id, kind="host_cdp",
            detail="Using your real Brave on the host",
            extra={"source": "host"},
        ))
        return {
            "status": "connected",
            "origin": host_bridge.origin_for_token(token),
        }

    return {
        "status": "needs_launch",
        "command": host_bridge.build_launch_command(token),
        "warning": host_bridge.security_warning(),
    }


@router.post("/host-session/stop")
async def host_session_stop(user: User = Depends(get_current_user)):
    """Revert to the container Brave (does NOT close host Brave)."""
    from lazyclaw.browser.browser_settings import update_browser_settings

    await update_browser_settings(_config, user.id, {"use_host_browser": "off"})
    event_bus.publish(event_bus.BrowserEvent(
        user_id=user.id, kind="host_cdp",
        detail="Host browser bridge stopped",
        extra={"source": "local"},
    ))
    return {"status": "stopped"}
