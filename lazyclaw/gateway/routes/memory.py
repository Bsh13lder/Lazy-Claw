"""Memory API — personal memories and daily logs."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.memory.daily_log import (
    delete_daily_log,
    generate_daily_summary,
    get_daily_log,
    list_daily_logs,
)
from lazyclaw.memory.personal import delete_memory, get_memories

_config = load_config()

router = APIRouter(prefix="/api/memory", tags=["memory"])

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(date: str) -> None:
    if not _DATE_PATTERN.match(date):
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD format")


# ---------------------------------------------------------------------------
# Personal memories
# ---------------------------------------------------------------------------

@router.get("/personal")
async def list_personal_memories(user: User = Depends(get_current_user)):
    """List personal memories for the current user."""
    memories = await get_memories(_config, user.id)
    return {"memories": memories}


@router.delete("/personal/{memory_id}")
async def delete_personal_memory(memory_id: str, user: User = Depends(get_current_user)):
    """Delete a personal memory by ID."""
    deleted = await delete_memory(_config, user.id, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted", "id": memory_id}


# ---------------------------------------------------------------------------
# Daily logs
# ---------------------------------------------------------------------------

@router.get("/daily-logs")
async def list_daily_logs_route(user: User = Depends(get_current_user)):
    """List recent daily logs."""
    logs = await list_daily_logs(_config, user.id)
    return {"logs": logs}


@router.get("/daily-logs/{date}")
async def get_daily_log_route(date: str, user: User = Depends(get_current_user)):
    """Get a specific daily log by date (YYYY-MM-DD)."""
    _validate_date(date)
    log = await get_daily_log(_config, user.id, date)
    if not log:
        raise HTTPException(status_code=404, detail="No log for this date")
    return log


@router.post("/daily-logs/{date}/generate")
async def generate_daily_log_route(date: str, user: User = Depends(get_current_user)):
    """Auto-summarize a day's conversations."""
    _validate_date(date)
    summary = await generate_daily_summary(_config, user.id, date)
    return {"date": date, "summary": summary}


@router.delete("/daily-logs/{date}")
async def delete_daily_log_route(date: str, user: User = Depends(get_current_user)):
    """Delete a daily log by date."""
    _validate_date(date)
    deleted = await delete_daily_log(_config, user.id, date)
    if not deleted:
        raise HTTPException(status_code=404, detail="No log for this date")
    return {"status": "deleted", "date": date}
