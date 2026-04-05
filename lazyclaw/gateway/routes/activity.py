"""Activity API — real-time agent and task status for the dashboard."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from lazyclaw.gateway.auth import User, get_current_user

router = APIRouter(prefix="/api/agents", tags=["activity"])

# Injected by app.py (same pattern as lane_queue / registry)
_team_lead = None
_task_runner = None


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
                "lane": t.lane,
                "status": t.status,
                "elapsed_s": round(now - t.started_at, 1),
                "current_step": t.current_step,
                "step_count": t.step_count,
            })
        for t in _team_lead.recent_tasks[:10]:
            duration = None
            if t.completed_at is not None:
                duration = round(t.completed_at - t.started_at, 1)
            recent.append({
                "task_id": t.task_id,
                "name": t.name,
                "description": t.description,
                "lane": t.lane,
                "status": t.status,
                "duration_s": duration,
                "result_preview": t.result_preview,
                "error": t.error or None,
            })

    # Merge background tasks from TaskRunner (if available)
    bg_running: list[dict] = []
    if _task_runner is not None:
        try:
            for task_id, name, elapsed in _task_runner.list_running(user.id):
                bg_running.append({
                    "task_id": task_id,
                    "name": name,
                    "lane": "background",
                    "status": "running",
                    "elapsed_s": round(elapsed, 1),
                })
        except Exception:
            pass  # TaskRunner may not have list_running for this user

    return {
        "active": active,
        "background": bg_running,
        "recent": recent,
    }
