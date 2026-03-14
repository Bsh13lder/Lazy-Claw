"""Browser automation API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from lazyclaw.browser.agent import BrowserAgentManager
from lazyclaw.browser.manager import BrowserSessionPool
from lazyclaw.browser import site_memory
from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/browser", tags=["browser"])

_config = load_config()
_session_pool = BrowserSessionPool(_config)
_agent_manager = BrowserAgentManager(_config, _session_pool)


# ── Request / Response models ────────────────────────────────────────────


class CreateTaskRequest(BaseModel):
    instruction: str
    max_steps: int = 0


class HelpRequest(BaseModel):
    response: str


class ContinueRequest(BaseModel):
    instruction: str


class UserActionRequest(BaseModel):
    type: str  # click, type, scroll, key
    x: int = 0
    y: int = 0
    text: str = ""
    key: str = ""
    delta_x: int = 0
    delta_y: int = 0


# ── Task endpoints ───────────────────────────────────────────────────────


@router.post("/tasks")
async def create_task(body: CreateTaskRequest, user: User = Depends(get_current_user)):
    """Create and start a browser automation task."""
    try:
        task_id = await _agent_manager.create_task(
            user.id, body.instruction, body.max_steps
        )
        await _agent_manager.start_task(task_id)
        return {"id": task_id, "status": "running"}
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc))


@router.get("/tasks")
async def list_tasks(
    limit: int = 50, user: User = Depends(get_current_user)
):
    """List user's browser tasks."""
    limit = min(max(limit, 1), 200)
    tasks = await _agent_manager.list_tasks(user.id, limit)
    return {"tasks": tasks}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: User = Depends(get_current_user)):
    """Get task details."""
    task = await _agent_manager.get_task(task_id, user.id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks/{task_id}/logs")
async def get_task_logs(
    task_id: str,
    after_id: str | None = None,
    user: User = Depends(get_current_user),
):
    """Get step-by-step logs. Use after_id for incremental polling."""
    logs = await _agent_manager.get_task_logs(task_id, user.id, after_id)
    return {"logs": logs}


@router.get("/tasks/{task_id}/live")
async def get_live_screenshot(
    task_id: str, user: User = Depends(get_current_user)
):
    """Get live screenshot of running task."""
    screenshot = _agent_manager.get_live_screenshot(task_id)
    if not screenshot:
        raise HTTPException(status_code=404, detail="No screenshot available")
    return Response(content=screenshot, media_type="image/png")


@router.post("/tasks/{task_id}/help")
async def provide_help(
    task_id: str, body: HelpRequest, user: User = Depends(get_current_user)
):
    """Respond to a task that needs help."""
    try:
        await _agent_manager.provide_help(task_id, user.id, body.response)
        return {"status": "help_provided"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/continue")
async def continue_task(
    task_id: str, body: ContinueRequest, user: User = Depends(get_current_user)
):
    """Continue a completed/failed task with new instruction."""
    try:
        await _agent_manager.continue_task(task_id, user.id, body.instruction)
        return {"status": "running"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, user: User = Depends(get_current_user)):
    """Cancel a running task."""
    try:
        await _agent_manager.cancel_task(task_id, user.id)
        return {"status": "cancelled"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Takeover endpoints ───────────────────────────────────────────────────


@router.post("/tasks/{task_id}/takeover")
async def request_takeover(
    task_id: str, user: User = Depends(get_current_user)
):
    """Request manual control of a running task."""
    try:
        await _agent_manager.request_takeover(task_id, user.id)
        return {"status": "takeover"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/release")
async def release_takeover(
    task_id: str, user: User = Depends(get_current_user)
):
    """Release manual control back to the agent."""
    try:
        await _agent_manager.release_takeover(task_id, user.id)
        return {"status": "running"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/action")
async def execute_user_action(
    task_id: str,
    body: UserActionRequest,
    user: User = Depends(get_current_user),
):
    """Execute a user action during takeover mode."""
    try:
        result = await _agent_manager.execute_user_action(
            task_id, user.id, body.model_dump()
        )
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Session endpoints ────────────────────────────────────────────────────


@router.post("/sessions/close")
async def close_session(user: User = Depends(get_current_user)):
    """Close the user's persistent browser session."""
    await _session_pool.close_session(user.id)
    return {"status": "closed"}


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
