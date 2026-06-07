"""Specialists REST CRUD API (ADR-0005 Phase 2).

Built-in specialists are read-only (forkable templates); custom specialists are
user-owned and stored encrypted in the ``specialists`` table. This router is a
thin HTTP layer over :mod:`lazyclaw.teams.specialist` — it validates input at
the boundary, maps the domain ``ValueError`` (built-in collision / built-in
delete) onto an HTTP 400 envelope, and shapes responses per the locked API
contract other surfaces (Web UI, mobile) build against.

Locked specialist JSON shape::

    {
        "name": str,
        "display_name": str,
        "system_prompt": str,
        "tools": list[str],
        "model": str | None,
        "include_scraper": bool,
        "is_builtin": bool,
    }
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.teams.specialist import (
    SpecialistConfig,
    delete_specialist,
    get_specialist,
    load_specialists,
    save_specialist,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/specialists", tags=["specialists"])

# A specialist name doubles as a stable identifier in URLs and as a filesystem
# slug downstream, so constrain it the same way the rest of the codebase slugs:
# lowercase letters, digits, and underscores only.
_SLUG_RE = re.compile(r"^[a-z0-9_]+$")


# ── Request models ────────────────────────────────────────────────────
#
# Fields are intentionally lenient at the pydantic layer (optional / typed)
# so that EVERY rejected body returns the contract's HTTP 400 envelope from
# our own validators, rather than FastAPI's default 422 for missing fields.


class CreateSpecialistRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str | None = None
    display_name: str | None = None
    system_prompt: str | None = None
    tools: list[str] = Field(default_factory=list)
    model: str | None = None
    include_scraper: bool = False


class UpdateSpecialistRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    display_name: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    model: str | None = None
    include_scraper: bool | None = None


# ── Helpers ───────────────────────────────────────────────────────────


def _to_shape(spec: SpecialistConfig) -> dict[str, Any]:
    """Project a :class:`SpecialistConfig` onto the locked wire shape."""
    return {
        "name": spec.name,
        "display_name": spec.display_name,
        "system_prompt": spec.system_prompt,
        "tools": list(spec.allowed_skills),
        "model": spec.preferred_model,
        "include_scraper": spec.include_scraper,
        "is_builtin": spec.is_builtin,
    }


def _error(message: str, status: int = 400) -> JSONResponse:
    """Build the contract error envelope with an explicit status code."""
    return JSONResponse(status_code=status, content={"ok": False, "error": message})


def _validate_name(name: str | None) -> str | None:
    if name is None or not name.strip():
        return "name is required"
    if not _SLUG_RE.fullmatch(name):
        return "name must be a slug: lowercase letters, digits, and underscores only"
    return None


def _validate_nonempty(field: str, value: str | None) -> str | None:
    if value is None or not value.strip():
        return f"{field} is required and must not be empty"
    return None


def _validate_tools(tools: list[str]) -> str | None:
    for tool in tools:
        if not isinstance(tool, str) or not tool.strip():
            return "tools must be a list of non-empty strings"
    return None


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("")
async def list_specialists(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
) -> dict[str, Any]:
    """List every specialist visible to the user (built-in + custom)."""
    specs = await load_specialists(config, user.id)
    return {"ok": True, "specialists": [_to_shape(s) for s in specs]}


@router.post("")
async def create_specialist(
    body: CreateSpecialistRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Create a user-owned custom specialist."""
    error = (
        _validate_name(body.name)
        or _validate_nonempty("display_name", body.display_name)
        or _validate_nonempty("system_prompt", body.system_prompt)
        or _validate_tools(body.tools)
    )
    if error:
        logger.info("Rejected create_specialist for user %s: %s", user.id, error)
        return _error(error)

    spec = SpecialistConfig(
        name=body.name,  # type: ignore[arg-type]  # validated non-None above
        display_name=body.display_name,  # type: ignore[arg-type]
        system_prompt=body.system_prompt,  # type: ignore[arg-type]
        allowed_skills=tuple(body.tools),
        preferred_model=body.model,
        is_builtin=False,
        include_scraper=body.include_scraper,
    )
    try:
        await save_specialist(config, user.id, spec)
    except ValueError as exc:
        # Built-in name collision (and any other domain rejection).
        logger.info("create_specialist rejected by domain for user %s: %s", user.id, exc)
        return _error(str(exc))

    logger.info("Created specialist '%s' for user %s", spec.name, user.id)
    return {"ok": True, "specialist": _to_shape(spec)}


@router.put("/{name}")
async def update_specialist(
    name: str,
    body: UpdateSpecialistRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Update an existing custom specialist. Built-ins are read-only (400)."""
    existing = await get_specialist(config, user.id, name)
    if existing is None:
        return _error(f"Specialist '{name}' not found", status=404)
    if existing.is_builtin:
        return _error("Cannot modify a built-in specialist")

    # Merge editable fields immutably — only fields present in the body win.
    new_display = body.display_name if body.display_name is not None else existing.display_name
    new_prompt = body.system_prompt if body.system_prompt is not None else existing.system_prompt
    new_tools = list(body.tools) if body.tools is not None else list(existing.allowed_skills)
    new_model = body.model if body.model is not None else existing.preferred_model
    new_scraper = (
        body.include_scraper if body.include_scraper is not None else existing.include_scraper
    )

    error = (
        _validate_nonempty("display_name", new_display)
        or _validate_nonempty("system_prompt", new_prompt)
        or _validate_tools(new_tools)
    )
    if error:
        logger.info("Rejected update_specialist '%s' for user %s: %s", name, user.id, error)
        return _error(error)

    updated = SpecialistConfig(
        name=name,
        display_name=new_display,
        system_prompt=new_prompt,
        allowed_skills=tuple(new_tools),
        preferred_model=new_model,
        is_builtin=False,
        include_scraper=new_scraper,
    )
    try:
        await save_specialist(config, user.id, updated)
    except ValueError as exc:
        logger.info("update_specialist rejected by domain for user %s: %s", user.id, exc)
        return _error(str(exc))

    logger.info("Updated specialist '%s' for user %s", name, user.id)
    return {"ok": True, "specialist": _to_shape(updated)}


@router.delete("/{name}")
async def remove_specialist(
    name: str,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Delete a custom specialist. Built-ins are read-only (400)."""
    try:
        deleted = await delete_specialist(config, user.id, name)
    except ValueError as exc:
        # Domain raises for built-in delete attempts.
        logger.info("delete_specialist rejected for user %s: %s", user.id, exc)
        return _error(str(exc))

    if not deleted:
        return _error(f"Specialist '{name}' not found", status=404)

    logger.info("Deleted specialist '%s' for user %s", name, user.id)
    return {"ok": True, "deleted": True}
