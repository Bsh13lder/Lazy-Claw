"""The task-pulse job protocol — one home for the ``[PULSE:...]`` shape.

A task "pulse" is a recurring check-in the agent fires against an in-flight
task ("how's the report going?"). It is driven by an ordinary ``agent_jobs``
cron row whose *instruction* carries a sentinel:

    ``[PULSE:<task_id>:<template_id>]``

The heartbeat daemon (``heartbeat/daemon.py:_check_due_jobs``) sniffs that
prefix and routes to ``_fire_task_pulse`` instead of enqueuing the instruction
to the brain.

That string used to be hand-built in five places (four in
``skills/builtin/progress_templates.py``, one in ``tasks/store.py``) plus parsed
in the daemon. Five copies of a wire format is five chances to drift — and the
prefix is used for *matching* (pause/resume/delete "the pulses belonging to this
task"), so a mismatched copy silently matches nothing and the caller reports
success. Everything that writes or matches the sentinel now goes through here.
"""
from __future__ import annotations

import logging

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

# The daemon's routing sentinel. Parsed in daemon._fire_task_pulse as
# instruction[len("[PULSE:"):].rstrip("]").split(":") — keep the shape in sync.
_SENTINEL = "PULSE"


def pulse_instruction(task_id: str, template_id: str) -> str:
    """The ``agent_jobs.instruction`` value that marks a row as a task pulse."""
    return f"[{_SENTINEL}:{task_id}:{template_id}]"


def pulse_job_prefix(task_id: str) -> str:
    """The ``instruction`` prefix matching EVERY pulse job for ``task_id``.

    Callers use it to find a task's pulse rows regardless of which template is
    attached (``instruction.startswith(pulse_job_prefix(task_id))``). The
    trailing colon matters: without it a task id that is a prefix of another
    would match the wrong task's pulses.
    """
    return f"[{_SENTINEL}:{task_id}:"


def is_pulse_instruction(instruction: str | None) -> bool:
    """True when ``instruction`` is a pulse sentinel rather than brain text."""
    return bool(instruction) and instruction.startswith(f"[{_SENTINEL}:")


async def create_pulse_job(
    config: Config,
    user_id: str,
    task_id: str,
    template_id: str,
    every: str,
) -> str:
    """Create the ``agent_jobs`` cron row that drives pulse firing.

    ``every`` is a cron expression (the task's ``check_every``). Returns the
    new job id.
    """
    from lazyclaw.heartbeat.orchestrator import create_job

    return await create_job(
        config, user_id,
        name=f"Pulse: {task_id[:8]}",
        instruction=pulse_instruction(task_id, template_id),
        cron_expression=every,
        job_type="cron",
        context=template_id,
    )


async def find_pulse_jobs(
    config: Config, user_id: str, task_id: str
) -> list[dict]:
    """Every pulse job belonging to ``task_id``. Empty list on any failure.

    Read-only lookup shared by the pause/resume/delete paths so they cannot
    disagree about what "this task's pulses" means.
    """
    try:
        from lazyclaw.heartbeat.orchestrator import list_jobs

        jobs = await list_jobs(config, user_id)
    except Exception:
        logger.debug("find_pulse_jobs: list_jobs failed", exc_info=True)
        return []
    prefix = pulse_job_prefix(task_id)
    return [j for j in jobs if (j.get("instruction") or "").startswith(prefix)]
