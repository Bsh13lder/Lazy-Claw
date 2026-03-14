"""Skills API — CRUD + AI generation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.skills.manager import (
    create_code_skill,
    create_instruction_skill,
    delete_user_skill_by_id,
    get_skill_by_id,
    list_user_skills,
    update_skill,
)

_config = load_config()

router = APIRouter(prefix="/api/skills", tags=["skills"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateSkillRequest(BaseModel):
    skill_type: str = "instruction"
    name: str
    description: str
    instruction: str | None = None
    code: str | None = None
    parameters_schema: dict | None = None


class UpdateSkillRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    instruction: str | None = None
    code: str | None = None
    parameters_schema: dict | None = None


class GenerateSkillRequest(BaseModel):
    description: str
    name: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_skills(user: User = Depends(get_current_user)):
    """List all skills for the current user."""
    skills = await list_user_skills(_config, user.id)
    return {"skills": skills}


@router.post("")
async def create_skill(body: CreateSkillRequest, user: User = Depends(get_current_user)):
    """Create an instruction or code skill."""
    if body.skill_type == "instruction":
        if not body.instruction:
            raise HTTPException(status_code=400, detail="Instruction is required for instruction skills")
        skill_id = await create_instruction_skill(
            _config, user.id, body.name, body.description, body.instruction,
        )
    elif body.skill_type == "code":
        if not body.code:
            raise HTTPException(status_code=400, detail="Code is required for code skills")
        skill_id = await create_code_skill(
            _config, user.id, body.name, body.description, body.code, body.parameters_schema,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown skill type: {body.skill_type}")

    return {"id": skill_id, "name": body.name, "type": body.skill_type}


@router.patch("/{skill_id}")
async def update_skill_route(
    skill_id: str,
    body: UpdateSkillRequest,
    user: User = Depends(get_current_user),
):
    """Update a skill's fields."""
    existing = await get_skill_by_id(_config, user.id, skill_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Skill not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = await update_skill(_config, user.id, skill_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Skill not found")

    return {"status": "updated", "id": skill_id}


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, user: User = Depends(get_current_user)):
    """Delete a skill by ID."""
    deleted = await delete_user_skill_by_id(_config, user.id, skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {"status": "deleted", "id": skill_id}


@router.post("/generate")
async def generate_skill(body: GenerateSkillRequest, user: User = Depends(get_current_user)):
    """AI-generate a code skill from description."""
    from lazyclaw.skills.sandbox import SandboxError
    from lazyclaw.skills.writer import generate_code_skill

    try:
        result = await generate_code_skill(
            _config, user.id, body.description, body.name,
        )
    except SandboxError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Skill generation failed: {exc}")

    return result
