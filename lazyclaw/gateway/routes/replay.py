"""Session Replay REST API — traces, playback, and sharing."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.replay.engine import (
    delete_trace,
    get_trace,
    get_trace_by_token,
    list_traces,
)
from lazyclaw.replay.sharing import create_share, list_shares, revoke_share

router = APIRouter(prefix="/api/replay", tags=["replay"])


class ShareRequest(BaseModel):
    trace_session_id: str
    expires_hours: int | None = Field(default=72, ge=1, le=168)


# ── Trace endpoints ──────────────────────────────────────────────────


@router.get("/traces")
async def list_user_traces(
    limit: int = 20,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """List recent trace sessions."""
    traces = await list_traces(config, user.id, limit=limit)
    data = [
        {
            "trace_session_id": t.trace_session_id,
            "entry_count": t.entry_count,
            "created_at": t.started_at,
            "started_at": t.started_at,
            "ended_at": t.ended_at,
            "entry_types": list(t.entry_types),
        }
        for t in traces
    ]
    return {"success": True, "data": data}


@router.get("/traces/{trace_session_id}")
async def get_trace_detail(
    trace_session_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """View a full trace (decrypted timeline)."""
    entries = await get_trace(config, user.id, trace_session_id)
    if not entries:
        return {"success": False, "error": "Trace not found"}

    data = [
        {
            "id": e.id,
            "sequence": e.sequence,
            "entry_type": e.entry_type,
            "content": e.content,
            "metadata": e.metadata,
            "created_at": e.created_at,
        }
        for e in entries
    ]
    return {"success": True, "data": data}


@router.delete("/traces/{trace_session_id}")
async def delete_trace_endpoint(
    trace_session_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Delete a trace and its shares."""
    deleted = await delete_trace(config, user.id, trace_session_id)
    if deleted == 0:
        return {"success": False, "error": "Trace not found"}
    return {"success": True, "data": {"deleted_entries": deleted}}


# ── Share endpoints ───────────────────────────────────────────────────


@router.post("/share")
async def create_share_endpoint(
    body: ShareRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Generate a shareable token for a trace."""
    try:
        share = await create_share(
            config, user.id, body.trace_session_id, body.expires_hours
        )
        return {"success": True, "data": share}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@router.get("/share/{token}")
async def view_shared_trace(
    token: str,
    config: Config = Depends(load_config),
):
    """View a trace via share token (no auth required)."""
    result = await get_trace_by_token(config, token)
    if not result:
        return {"success": False, "error": "Invalid or expired share token"}

    entries, _ = result
    data = [
        {
            "id": e.id,
            "sequence": e.sequence,
            "entry_type": e.entry_type,
            "content": e.content,
            "metadata": e.metadata,
            "created_at": e.created_at,
        }
        for e in entries
    ]
    return {"success": True, "data": data}


@router.get("/shares")
async def list_user_shares(
    trace_session_id: str | None = None,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """List share tokens for the user."""
    shares = await list_shares(config, user.id, trace_session_id)
    return {"success": True, "data": shares}


@router.delete("/shares/{share_id}")
async def revoke_share_endpoint(
    share_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Revoke a share token."""
    revoked = await revoke_share(config, user.id, share_id)
    if not revoked:
        return {"success": False, "error": "Share not found"}
    return {"success": True, "data": {"revoked": share_id}}
