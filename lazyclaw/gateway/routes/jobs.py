"""Jobs API — scheduled and one-off job management."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user

logger = logging.getLogger(__name__)

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


# ── NL job drafting ─────────────────────────────────────────────────────


_JOB_DRAFT_SYSTEM = (
    "You turn one-line descriptions of scheduled work into a Job draft. "
    "Output JSON only — no markdown, no prose. Schema: "
    "{\"name\": str (<=60 chars), \"instruction\": str (what the agent should do), "
    "\"job_type\": \"cron\" | \"one_off\", "
    "\"cron_expression\": str | null (5-field cron, null when job_type=one_off), "
    "\"context\": str | null}. "
    "Rules: "
    "- Use cron for recurring language (every day, weekly, each Monday). Examples: "
    "\"0 9 * * *\" = daily 9am, \"0 9 * * 1\" = Monday 9am, \"*/30 * * * *\" = every 30 min. "
    "- Use one_off for one-time asks (tomorrow, next Monday, in 2 hours). Leave cron_expression null. "
    "- Keep name short and imperative. Put the full detail in instruction. "
    "- Interpret all times in the user's local timezone (UTC if unspecified). "
    "- Never invent data — if the user didn't specify a time, set cron_expression to null "
    "  and ask nothing; the user will fix it in the form."
)


@router.post("/from-prompt")
async def draft_job_from_prompt(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    """Draft a Job from a natural-language prompt. Non-persisted — the UI
    pre-fills the Create Job form with the result so the user can review."""
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if len(prompt) > 800:
        raise HTTPException(status_code=400, detail="prompt too long (max 800 chars)")

    from lazyclaw.llm.providers.base import LLMMessage
    from lazyclaw.llm.router import LLMRouter

    router_llm = LLMRouter(_config)
    messages = [
        LLMMessage(role="system", content=_JOB_DRAFT_SYSTEM),
        LLMMessage(role="user", content=f"User's request: {prompt}\n\nReturn JSON."),
    ]
    model = getattr(_config, "worker_model", None)

    try:
        response = await router_llm.chat(messages, model=model, user_id=user.id)
    except Exception as exc:
        logger.warning("job from-prompt LLM call failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Draft failed: {exc}")

    raw = (response.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("job from-prompt: non-JSON response: %s", raw[:200])
        return {
            "draft": {
                "name": prompt[:60],
                "instruction": prompt,
                "job_type": "cron",
                "cron_expression": "",
                "context": None,
            }
        }

    job_type = data.get("job_type")
    if job_type not in ("cron", "one_off"):
        job_type = "cron"

    cron = data.get("cron_expression")
    if job_type == "one_off":
        cron = None
    elif cron is not None:
        cron = str(cron).strip() or None

    draft = {
        "name": str(data.get("name") or prompt[:60])[:60],
        "instruction": str(data.get("instruction") or prompt),
        "job_type": job_type,
        "cron_expression": cron,
        "context": (str(data["context"]) if data.get("context") else None),
    }
    return {"draft": draft}
