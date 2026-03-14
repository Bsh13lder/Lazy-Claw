"""Jobs API — scheduled and one-off job management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user

_config = load_config()

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class CreateJobRequest(BaseModel):
    name: str
    instruction: str
    job_type: str = "cron"
    cron_expression: str | None = None
    context: str | None = None


class UpdateJobRequest(BaseModel):
    name: str | None = None
    instruction: str | None = None
    cron_expression: str | None = None
    context: str | None = None


@router.get("")
async def list_jobs(user: User = Depends(get_current_user)):
    """List user's jobs."""
    from lazyclaw.heartbeat.orchestrator import list_jobs

    jobs = await list_jobs(_config, user.id)
    return {"jobs": jobs}


@router.post("")
async def create_job(body: CreateJobRequest, user: User = Depends(get_current_user)):
    """Create a new job."""
    from lazyclaw.heartbeat.orchestrator import create_job

    if body.job_type == "cron" and not body.cron_expression:
        raise HTTPException(
            status_code=422,
            detail="cron_expression is required when job_type is 'cron'",
        )

    job_id = await create_job(
        _config,
        user.id,
        body.name,
        body.instruction,
        body.job_type,
        body.cron_expression,
        body.context,
    )
    return {"id": job_id, "status": "ok"}


@router.get("/{job_id}")
async def get_job(job_id: str, user: User = Depends(get_current_user)):
    """Get job details."""
    from lazyclaw.heartbeat.orchestrator import get_job

    job = await get_job(_config, user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.patch("/{job_id}")
async def update_job(
    job_id: str,
    body: UpdateJobRequest,
    user: User = Depends(get_current_user),
):
    """Update job fields."""
    from lazyclaw.heartbeat.orchestrator import update_job

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update")

    updated = await update_job(_config, user.id, job_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "ok"}


@router.delete("/{job_id}")
async def delete_job(job_id: str, user: User = Depends(get_current_user)):
    """Delete a job."""
    from lazyclaw.heartbeat.orchestrator import delete_job

    deleted = await delete_job(_config, user.id, job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "deleted"}


@router.post("/{job_id}/pause")
async def pause_job(job_id: str, user: User = Depends(get_current_user)):
    """Pause a job."""
    from lazyclaw.heartbeat.orchestrator import pause_job

    paused = await pause_job(_config, user.id, job_id)
    if not paused:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "paused"}


@router.post("/{job_id}/resume")
async def resume_job(job_id: str, user: User = Depends(get_current_user)):
    """Resume a paused job."""
    from lazyclaw.heartbeat.orchestrator import resume_job

    resumed = await resume_job(_config, user.id, job_id)
    if not resumed:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "resumed"}
