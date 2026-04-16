"""Activity API — real-time agent and task status for the dashboard."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["activity"])

# Injected by app.py (same pattern as lane_queue / registry)
_team_lead = None
_task_runner = None

# Monotonic → wall-clock anchor (set at module load)
_mono_anchor = time.monotonic()
_wall_anchor = time.time()


def _mono_to_iso(mono: float) -> str:
    """Convert monotonic timestamp to ISO-8601 wall-clock string."""
    wall = _wall_anchor + (mono - _mono_anchor)
    return datetime.fromtimestamp(wall, tz=timezone.utc).isoformat()


def set_activity_deps(team_lead, task_runner) -> None:
    """Called by app.py to inject shared TeamLead and TaskRunner."""
    global _team_lead, _task_runner
    _team_lead = team_lead
    _task_runner = task_runner


@router.get("/status")
async def get_agent_status(user: User = Depends(get_current_user)):
    """Return active and recent tasks from TeamLead + TaskRunner."""
    now = time.monotonic()

    active: list[dict] = []
    recent: list[dict] = []

    if _team_lead is not None:
        for t in _team_lead.active_tasks:
            active.append({
                "task_id": t.task_id,
                "name": t.name,
                "description": t.description,
                "instruction": t.instruction_full or t.description,
                "lane": t.lane,
                "status": t.status,
                "elapsed_s": round(now - t.started_at, 1),
                "current_step": t.current_step,
                "current_tool": t.current_tool or t.current_step,
                "step_count": t.step_count,
                "phase": t.phase,
                "recent_tools": list(t.recent_tools),
            })
        for t in _team_lead.recent_tasks[:10]:
            duration = None
            if t.completed_at is not None:
                duration = round(t.completed_at - t.started_at, 1)
            recent.append({
                "task_id": t.task_id,
                "name": t.name,
                "description": t.description,
                "instruction": t.instruction_full or t.description,
                "lane": t.lane,
                "status": t.status,
                "duration_s": duration,
                "result_preview": t.result_preview,
                "result": t.result_full or t.result_preview,
                "error": t.error or None,
            })

    # Merge background tasks from TaskRunner (if available)
    bg_running: list[dict] = []
    if _task_runner is not None:
        try:
            for task in _task_runner.list_running(user.id):
                bg_running.append({
                    "task_id": task["id"],
                    "name": task["name"],
                    "lane": "background",
                    "status": "running",
                    "elapsed_s": round(task["elapsed_seconds"], 1),
                })
        except Exception:
            logger.warning("Failed to list running background tasks", exc_info=True)

    return {
        "active": active,
        "background": bg_running,
        "recent": recent,
    }


class CancelRequest(BaseModel):
    task_id: str


@router.post("/cancel")
async def cancel_task(
    body: CancelRequest,
    user: User = Depends(get_current_user),
):
    """Cancel a running task — foreground, specialist, or background.

    Tries foreground/specialist first via TeamLead cancel tokens, then
    falls back to TaskRunner's asyncio-task cancel for background jobs.
    """
    fired = False

    # Foreground / specialist via TeamLead cancel token
    if _team_lead is not None:
        try:
            fired = _team_lead.request_cancel(body.task_id, user.id)
        except Exception as exc:
            logger.warning("TeamLead.request_cancel failed: %s", exc)

    # Background via TaskRunner
    if not fired and _task_runner is not None:
        try:
            fired = await _task_runner.cancel(body.task_id, user.id)
        except Exception as exc:
            logger.warning("TaskRunner.cancel failed: %s", exc)

    if fired:
        return {"success": True, "data": {"task_id": body.task_id, "status": "cancelling"}}
    return {"success": False, "error": "Task not found or not cancellable"}


@router.post("/cancel-all")
async def cancel_all_tasks(user: User = Depends(get_current_user)):
    """Cancel all running tasks for the current user — foreground + background."""
    cancelled: list[dict] = []

    # Foreground + specialist via TeamLead
    if _team_lead is not None:
        for t in list(_team_lead.active_tasks):
            try:
                if _team_lead.request_cancel(t.task_id, user.id):
                    cancelled.append({"task_id": t.task_id, "name": t.name})
            except Exception as exc:
                logger.warning("cancel_all: TeamLead fire failed: %s", exc)

    # Background via TaskRunner
    if _task_runner is not None:
        try:
            for task in _task_runner.list_running(user.id):
                if await _task_runner.cancel(task["id"], user.id):
                    cancelled.append({"task_id": task["id"], "name": task["name"]})
        except Exception as exc:
            logger.warning("cancel_all: TaskRunner sweep failed: %s", exc)

    return {"success": True, "data": {"cancelled": cancelled, "count": len(cancelled)}}


@router.get("/activity/feed")
async def get_activity_feed(
    limit: int = 30,
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Merged activity feed from tasks + audit log."""
    events: list[dict] = []

    # Tasks from TeamLead
    if _team_lead is not None:
        for t in _team_lead.recent_tasks[:limit]:
            duration_ms = None
            if t.completed_at is not None:
                duration_ms = round((t.completed_at - t.started_at) * 1000)

            ev_type = "specialist" if t.lane == "specialist" else "task"
            if t.status == "failed":
                ev_type = "error"

            events.append({
                "id": f"task-{t.task_id}",
                "type": ev_type,
                "title": t.name,
                "detail": t.description or t.result_preview or "",
                "status": t.status,
                "timestamp": _mono_to_iso(t.started_at),
                "duration_ms": duration_ms,
                "metadata": {
                    "lane": t.lane,
                    "current_step": t.current_step,
                    "step_count": t.step_count,
                    "error": t.error or None,
                },
            })

    # Audit log entries
    try:
        from lazyclaw.permissions.audit import query_log

        audit_entries = await query_log(config, user.id, limit=limit)
        for e in audit_entries:
            ev_type = "tool_execution" if e.action == "execute" else "approval"
            events.append({
                "id": f"audit-{e.id}",
                "type": ev_type,
                "title": e.skill_name or e.action,
                "detail": e.result_summary or "",
                "status": "done",
                "timestamp": e.created_at,
                "duration_ms": None,
                "metadata": {"source": e.source, "action": e.action},
            })
    except Exception:
        logger.warning("Failed to query audit log for activity feed", exc_info=True)

    # Sort by timestamp desc and limit
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return {"success": True, "data": events[:limit]}


@router.get("/metrics")
async def get_agent_metrics(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Aggregated agent performance metrics."""
    now = time.monotonic()
    one_hour_ago = now - 3600

    total_completed = 0
    total_failed = 0
    total_duration = 0.0
    duration_count = 0
    tasks_last_hour = 0

    if _team_lead is not None:
        for t in _team_lead.recent_tasks:
            if t.status == "done":
                total_completed += 1
                if t.completed_at is not None:
                    d = t.completed_at - t.started_at
                    total_duration += d
                    duration_count += 1
            elif t.status == "failed":
                total_failed += 1

            if t.started_at >= one_hour_ago:
                tasks_last_hour += 1

    total = total_completed + total_failed
    avg_duration = round(total_duration / duration_count, 1) if duration_count > 0 else 0
    success_rate = round(total_completed / total * 100, 1) if total > 0 else 0

    # Tool calls today from audit log
    tool_calls_today = 0
    try:
        from lazyclaw.permissions.audit import query_log

        today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT00:00:00")
        today_entries = await query_log(
            config, user.id, action_filter="execute", since=today_str, limit=500
        )
        tool_calls_today = len(today_entries)
    except Exception:
        logger.warning("Failed to count today's tool calls", exc_info=True)

    return {
        "success": True,
        "data": {
            "avg_duration_s": avg_duration,
            "success_rate": success_rate,
            "total_completed": total_completed,
            "total_failed": total_failed,
            "tasks_last_hour": tasks_last_hour,
            "tool_calls_today": tool_calls_today,
        },
    }
