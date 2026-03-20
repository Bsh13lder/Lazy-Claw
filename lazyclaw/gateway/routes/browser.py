"""Browser API routes — site memory endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from lazyclaw.browser import site_memory
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
