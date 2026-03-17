"""Job and reminder skills — natural language scheduling.

Allows the agent to create/list/manage cron jobs and one-time reminders
through conversation. Uses the existing heartbeat daemon for execution.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class ScheduleJobSkill(BaseSkill):
    """Create a recurring scheduled job (cron)."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "schedule_job"

    @property
    def description(self) -> str:
        return (
            "Create a recurring scheduled job that runs on a cron schedule. "
            "Convert the user's natural language schedule to a cron expression "
            "(5 fields: minute hour day month weekday). Examples: "
            "'every hour' = '0 * * * *', "
            "'every morning at 9' = '0 9 * * *', "
            "'every Monday at 8:30' = '30 8 * * 1'. "
            "The instruction is what the agent should do each time."
        )

    @property
    def category(self) -> str:
        return "utility"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short name for the job (e.g. 'Bitcoin price check')",
                },
                "instruction": {
                    "type": "string",
                    "description": (
                        "What the agent should do each time the job runs. "
                        "Written as a clear instruction."
                    ),
                },
                "cron_expression": {
                    "type": "string",
                    "description": (
                        "5-field cron expression: minute hour day month weekday. "
                        "e.g. '0 9 * * *' for daily at 9am, "
                        "'*/30 * * * *' for every 30 minutes."
                    ),
                },
            },
            "required": ["name", "instruction", "cron_expression"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.cron import is_valid
        from lazyclaw.heartbeat.orchestrator import create_job

        name = params.get("name", "").strip()
        instruction = params.get("instruction", "").strip()
        cron_expr = params.get("cron_expression", "").strip()

        if not name or not instruction or not cron_expr:
            return "Missing required fields: name, instruction, and cron_expression."

        if not is_valid(cron_expr):
            return (
                f"Invalid cron expression: '{cron_expr}'. "
                f"Use 5 fields: minute hour day month weekday. "
                f"Example: '0 9 * * *' for daily at 9am."
            )

        try:
            job_id = await create_job(
                self._config,
                user_id,
                name=name,
                instruction=instruction,
                job_type="cron",
                cron_expression=cron_expr,
            )
            return (
                f"Scheduled job '{name}' created.\n"
                f"Schedule: {cron_expr}\n"
                f"Instruction: {instruction}\n"
                f"ID: {job_id}\n"
                f"The heartbeat daemon will run this automatically."
            )
        except Exception as e:
            logger.error("Failed to create job: %s", e, exc_info=True)
            return f"Failed to create job: {e}"


class SetReminderSkill(BaseSkill):
    """Set a one-time reminder at a specific time."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_reminder"

    @property
    def description(self) -> str:
        return (
            "Set a one-time reminder that fires at a specific time. "
            "Convert the user's request to an ISO datetime. Examples: "
            "'in 2 hours' → calculate from now, "
            "'at 5pm' → today at 17:00, "
            "'tomorrow at 9am' → next day at 09:00. "
            "The reminder will be delivered via Telegram (if connected) "
            "and also shown in the chat. After firing, it auto-deletes."
        )

    @property
    def category(self) -> str:
        return "utility"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The reminder message (e.g. 'Buy milk')",
                },
                "remind_at": {
                    "type": "string",
                    "description": (
                        "When to fire the reminder in ISO 8601 format "
                        "(e.g. '2026-03-17T17:00:00'). "
                        "Calculate this from the user's request."
                    ),
                },
            },
            "required": ["message", "remind_at"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.orchestrator import create_job

        message = params.get("message", "").strip()
        remind_at = params.get("remind_at", "").strip()

        if not message or not remind_at:
            return "Missing required fields: message and remind_at."

        # Validate the datetime
        try:
            dt = datetime.fromisoformat(remind_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt <= datetime.now(timezone.utc):
                return (
                    f"Reminder time '{remind_at}' is in the past. "
                    f"Please set a future time."
                )
        except ValueError:
            return (
                f"Invalid datetime format: '{remind_at}'. "
                f"Use ISO 8601 format (e.g. '2026-03-17T17:00:00')."
            )

        try:
            job_id = await create_job(
                self._config,
                user_id,
                name=f"Reminder: {message[:50]}",
                instruction=message,
                job_type="reminder",
                context=remind_at,
            )

            # Store next_run directly for the daemon to pick up
            from lazyclaw.db.connection import db_session

            async with db_session(self._config) as db:
                await db.execute(
                    "UPDATE agent_jobs SET next_run = ? WHERE id = ?",
                    (dt.isoformat(), job_id),
                )
                await db.commit()

            # Format display time
            display_time = dt.strftime("%B %d at %I:%M %p")

            return (
                f"Reminder set for {display_time}.\n"
                f"Message: {message}\n"
                f"I'll notify you via Telegram and chat when it's time."
            )
        except Exception as e:
            logger.error("Failed to set reminder: %s", e, exc_info=True)
            return f"Failed to set reminder: {e}"


class ListJobsSkill(BaseSkill):
    """List all scheduled jobs and reminders."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_jobs"

    @property
    def description(self) -> str:
        return (
            "List all scheduled jobs (cron) and reminders for the user. "
            "Shows name, schedule/time, status, and last run."
        )

    @property
    def category(self) -> str:
        return "utility"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.orchestrator import list_jobs

        try:
            jobs = await list_jobs(self._config, user_id)
        except Exception as e:
            return f"Failed to list jobs: {e}"

        if not jobs:
            return "No scheduled jobs or reminders."

        lines = [f"Scheduled jobs ({len(jobs)}):"]
        for job in jobs:
            job_type = job.get("job_type", "cron")
            name = job.get("name", "?")
            status = job.get("status", "?")
            status_icon = "\u2713" if status == "active" else "\u23f8" if status == "paused" else "\u2717"

            if job_type == "reminder":
                next_run = job.get("next_run", "?")
                lines.append(f"  {status_icon} {name}")
                lines.append(f"    Type: Reminder (one-time)")
                lines.append(f"    Fires: {next_run}")
            else:
                cron = job.get("cron_expression", "?")
                last_run = job.get("last_run", "never")
                next_run = job.get("next_run", "?")
                lines.append(f"  {status_icon} {name}")
                lines.append(f"    Schedule: {cron}")
                lines.append(f"    Last run: {last_run}")
                lines.append(f"    Next run: {next_run}")

            lines.append(f"    Status: {status}")
            lines.append("")

        return "\n".join(lines)


class ManageJobSkill(BaseSkill):
    """Pause, resume, or delete a job by name."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "manage_job"

    @property
    def description(self) -> str:
        return (
            "Manage a scheduled job or reminder: pause, resume, or delete it. "
            "Match the job by name (partial match works)."
        )

    @property
    def category(self) -> str:
        return "utility"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pause", "resume", "delete"],
                    "description": "What to do with the job",
                },
                "job_name": {
                    "type": "string",
                    "description": (
                        "Name or partial name of the job to manage. "
                        "Fuzzy matched against existing jobs."
                    ),
                },
            },
            "required": ["action", "job_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.orchestrator import (
            delete_job, list_jobs, pause_job, resume_job,
        )

        action = params.get("action", "").strip()
        job_name = params.get("job_name", "").strip().lower()

        if not action or not job_name:
            return "Missing required fields: action and job_name."

        try:
            jobs = await list_jobs(self._config, user_id)
        except Exception as e:
            return f"Failed to list jobs: {e}"

        # Fuzzy match by name
        match = None
        for job in jobs:
            name = (job.get("name") or "").lower()
            if job_name in name or name in job_name:
                match = job
                break

        if not match:
            available = ", ".join(j.get("name", "?") for j in jobs[:5])
            return f"No job matching '{job_name}'. Available: {available}"

        job_id = match["id"]
        display_name = match.get("name", "?")

        try:
            if action == "pause":
                ok = await pause_job(self._config, user_id, job_id)
                return f"Paused '{display_name}'." if ok else f"Could not pause (already paused?)."
            elif action == "resume":
                ok = await resume_job(self._config, user_id, job_id)
                return f"Resumed '{display_name}'." if ok else f"Could not resume (already active?)."
            elif action == "delete":
                ok = await delete_job(self._config, user_id, job_id)
                return f"Deleted '{display_name}'." if ok else f"Could not delete."
            else:
                return f"Unknown action: {action}. Use: pause, resume, delete."
        except Exception as e:
            logger.error("manage_job %s failed: %s", action, e, exc_info=True)
            return f"Error: {e}"
