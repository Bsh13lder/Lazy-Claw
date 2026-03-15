"""Context compression REST API — stats and manual re-compression."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.memory.compressor import force_recompress, get_compression_stats

router = APIRouter(prefix="/api/compression", tags=["compression"])


class ForceRecompressRequest(BaseModel):
    chat_session_id: str | None = None


@router.get("/stats")
async def stats(
    chat_session_id: str | None = None,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Get compression statistics for the current user."""
    data = await get_compression_stats(config, user.id, chat_session_id)
    return {"success": True, "data": data}


@router.post("/force")
async def force(
    body: ForceRecompressRequest,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Delete existing summaries so they regenerate on next chat."""
    from lazyclaw.llm.eco_router import EcoRouter
    from lazyclaw.llm.router import LLMRouter

    eco_router = EcoRouter(config, LLMRouter(config))
    deleted = await force_recompress(config, eco_router, user.id, body.chat_session_id)
    return {
        "success": True,
        "data": {
            "deleted_summaries": deleted,
            "message": "Summaries will regenerate on next chat message",
        },
    }
