"""Teams REST API — settings, specialist management, session history."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.teams.settings import get_team_settings, update_team_settings
from lazyclaw.teams.specialist import (
    SpecialistConfig,
    delete_specialist,
    get_specialist,
    load_specialists,
    save_specialist,
)
from lazyclaw.teams.conversation import get_session, list_sessions

router = APIRouter(prefix="/api/teams", tags=["teams"])


# ── Request models ────────────────────────────────────────────────────


class UpdateTeamSettingsRequest(BaseModel):
    mode: str | None = None
    critic_mode: str | None = None
    max_parallel: int | None = None
    specialist_timeout: int | None = None


class CreateSpecialistRequest(BaseModel):
    name: str
    display_name: str
    system_prompt: str
    allowed_skills: list[str]
    preferred_model: str | None = None


class UpdateSpecialistRequest(BaseModel):
    display_name: str | None = None
    system_prompt: str | None = None
    allowed_skills: list[str] | None = None
    preferred_model: str | None = None


# ── Settings endpoints ────────────────────────────────────────────────


@router.get("/settings")
async def get_settings(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Get user's team mode settings."""
    settings = await get_team_settings(config, user.id)
    return {"success": True, "data": settings}


@router.patch("/settings")
async def update_settings(
    body: UpdateTeamSettingsRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Update team mode settings."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"success": False, "error": "No fields to update"}
    try:
        new_settings = await update_team_settings(config, user.id, updates)
        return {"success": True, "data": new_settings}
    except ValueError as e:
        return {"success": False, "error": str(e)}


# ── Specialist endpoints ──────────────────────────────────────────────


@router.get("/specialists")
async def list_all_specialists(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """List all specialists (built-in + custom)."""
    specs = await load_specialists(config, user.id)
    data = [
        {
            "name": s.name,
            "display_name": s.display_name,
            "system_prompt": s.system_prompt,
            "allowed_skills": list(s.allowed_skills),
            "preferred_model": s.preferred_model,
            "is_builtin": s.is_builtin,
        }
        for s in specs
    ]
    return {"success": True, "data": data}


@router.post("/specialists")
async def create_specialist(
    body: CreateSpecialistRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Create a custom specialist."""
    spec = SpecialistConfig(
        name=body.name,
        display_name=body.display_name,
        system_prompt=body.system_prompt,
        allowed_skills=tuple(body.allowed_skills),
        preferred_model=body.preferred_model,
        is_builtin=False,
    )
    try:
        record_id = await save_specialist(config, user.id, spec)
        return {"success": True, "data": {"id": record_id, "name": spec.name}}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@router.patch("/specialists/{name}")
async def update_specialist_endpoint(
    name: str,
    body: UpdateSpecialistRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Update a custom specialist."""
    existing = await get_specialist(config, user.id, name)
    if not existing:
        return {"success": False, "error": f"Specialist '{name}' not found"}
    if existing.is_builtin:
        return {"success": False, "error": "Cannot modify built-in specialists"}

    updated = SpecialistConfig(
        name=name,
        display_name=body.display_name or existing.display_name,
        system_prompt=body.system_prompt or existing.system_prompt,
        allowed_skills=tuple(body.allowed_skills) if body.allowed_skills else existing.allowed_skills,
        preferred_model=body.preferred_model if body.preferred_model is not None else existing.preferred_model,
        is_builtin=False,
    )
    try:
        record_id = await save_specialist(config, user.id, updated)
        return {"success": True, "data": {"id": record_id, "name": name}}
    except ValueError as e:
        return {"success": False, "error": str(e)}


@router.delete("/specialists/{name}")
async def delete_specialist_endpoint(
    name: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Delete a custom specialist."""
    try:
        deleted = await delete_specialist(config, user.id, name)
        if not deleted:
            return {"success": False, "error": f"Specialist '{name}' not found"}
        return {"success": True, "data": {"deleted": name}}
    except ValueError as e:
        return {"success": False, "error": str(e)}


# ── Session endpoints ─────────────────────────────────────────────────


@router.get("/sessions")
async def list_team_sessions(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """List recent team sessions."""
    sessions = await list_sessions(config, user.id)
    return {"success": True, "data": sessions}


@router.get("/sessions/{session_id}")
async def get_team_session(
    session_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """View a team session's conversation (decrypted)."""
    messages = await get_session(config, user.id, session_id)
    if not messages:
        return {"success": False, "error": "Session not found"}

    data = [
        {
            "id": m.id,
            "from_agent": m.from_agent,
            "to_agent": m.to_agent,
            "message_type": m.message_type,
            "content": m.content,
            "created_at": m.created_at,
        }
        for m in messages
    ]
    return {"success": True, "data": data}
