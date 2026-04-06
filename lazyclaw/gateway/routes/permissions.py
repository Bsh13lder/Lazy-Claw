"""Permissions REST API — settings, approvals, and skill permission resolution."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.permissions.approvals import (
    approve_request,
    deny_request,
    get_pending,
    get_request,
)
from lazyclaw.permissions.audit import query_log
from lazyclaw.permissions.checker import PermissionChecker
from lazyclaw.permissions.settings import get_permission_settings, update_permission_settings
from lazyclaw.skills.registry import SkillRegistry

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


class UpdatePermissionsRequest(BaseModel):
    category_defaults: dict[str, str] | None = None
    skill_overrides: dict[str, str] | None = None
    auto_approve_timeout: int | None = None
    require_approval_for_heartbeat: bool | None = None


class SkillOverrideRequest(BaseModel):
    level: str  # allow | ask | deny


# --- Settings ---


@router.get("/settings")
async def get_settings(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Get user's permission settings."""
    settings = await get_permission_settings(config, user.id)
    return {"success": True, "data": settings}


@router.patch("/settings")
async def update_settings(
    body: UpdatePermissionsRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Update permission settings."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"success": False, "error": "No fields to update"}
    try:
        new_settings = await update_permission_settings(config, user.id, updates)
        return {"success": True, "data": new_settings}
    except ValueError as e:
        return {"success": False, "error": str(e)}


# --- Skills with resolved permissions ---


@router.get("/skills")
async def list_skills_permissions(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """List all skills with their resolved permission level for this user."""
    registry = SkillRegistry()
    registry.register_defaults(config=config)
    checker = PermissionChecker(config, registry)
    results = await checker.resolve_all(user.id)

    skills = [
        {
            "name": r.skill_name,
            "level": r.level,
            "source": r.source,
            "category": (
                registry.get(r.skill_name).category
                if registry.get(r.skill_name)
                else "unknown"
            ),
        }
        for r in results
    ]
    return {"success": True, "data": skills}


@router.patch("/skills/{skill_name}")
async def override_skill(
    skill_name: str,
    body: SkillOverrideRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Set a permission override for a specific skill."""
    if body.level not in {"allow", "ask", "deny"}:
        raise HTTPException(
            status_code=400, detail="Level must be 'allow', 'ask', or 'deny'"
        )

    settings = await get_permission_settings(config, user.id)
    overrides = dict(settings.get("skill_overrides", {}))
    overrides[skill_name] = body.level
    await update_permission_settings(config, user.id, {"skill_overrides": overrides})
    return {"success": True, "data": {"skill": skill_name, "level": body.level}}


@router.delete("/skills/{skill_name}")
async def remove_skill_override(
    skill_name: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Remove a permission override for a skill (falls back to category default)."""
    settings = await get_permission_settings(config, user.id)
    overrides = dict(settings.get("skill_overrides", {}))
    if skill_name not in overrides:
        return {"success": False, "error": f"No override exists for '{skill_name}'"}
    del overrides[skill_name]
    await update_permission_settings(config, user.id, {"skill_overrides": overrides})
    return {"success": True, "data": {"skill": skill_name, "level": "removed"}}


# --- Approvals ---


@router.get("/approvals")
async def list_approvals(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """List pending approval requests for the current user."""
    pending = await get_pending(config, user.id)
    items = [
        {
            "id": a.id,
            "skill_name": a.skill_name,
            "status": a.status,
            "source": a.source,
            "expires_at": a.expires_at,
            "created_at": a.created_at,
        }
        for a in pending
    ]
    return {"success": True, "data": items}


@router.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Approve a pending request."""
    request = await get_request(config, approval_id)
    if not request:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if request.user_id != user.id:
        raise HTTPException(
            status_code=403, detail="Not authorized to approve this request"
        )
    if request.status != "pending":
        return {"success": False, "error": f"Request is already {request.status}"}

    updated = await approve_request(config, approval_id, user.id)
    return {"success": True, "data": {"id": updated.id, "status": updated.status}}


@router.post("/approvals/{approval_id}/deny")
async def deny(
    approval_id: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Deny a pending request."""
    request = await get_request(config, approval_id)
    if not request:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if request.user_id != user.id:
        raise HTTPException(
            status_code=403, detail="Not authorized to deny this request"
        )
    if request.status != "pending":
        return {"success": False, "error": f"Request is already {request.status}"}

    updated = await deny_request(config, approval_id, user.id)
    return {"success": True, "data": {"id": updated.id, "status": updated.status}}


# --- Audit Log ---


@router.get("/audit")
async def get_audit_log(
    action: str | None = None,
    since: str | None = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Query audit log entries for the current user."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 500")
    entries = await query_log(config, user.id, action_filter=action, since=since, limit=limit)
    items = [
        {
            "id": e.id,
            "action": e.action,
            "skill_name": e.skill_name,
            "result_summary": e.result_summary,
            "source": e.source,
            "created_at": e.created_at,
        }
        for e in entries
    ]
    return {"success": True, "data": items, "count": len(items)}
