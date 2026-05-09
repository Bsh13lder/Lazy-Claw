"""Progress template skills — apply, save, list, pause/resume.

These are the agent-facing surface for the progress-tracking pulse
system. Each wraps the underlying CRUD in
``lazyclaw/tasks/progress_templates.py`` so the brain can opt a task
into pulses ("apply general pulse to upwork") or save a custom pulse
template ("save coding pulse: every 20m, ask 'what compiled?'").

The agent_jobs row that drives the actual cron firing is created here
(in ``apply_progress_template``) and paused/resumed via the existing
heartbeat orchestrator helpers.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────


async def _create_pulse_job(
    config, user_id: str, task_id: str, template_id: str, every: str,
) -> str:
    """Create the agent_jobs cron row that drives pulse firing.

    Instruction shape ``[PULSE:<task_id>:<template_id>]`` — the
    daemon's ``_check_due_jobs`` detects this prefix and routes to
    ``_fire_task_pulse`` instead of enqueuing to the brain.
    """
    from lazyclaw.heartbeat.orchestrator import create_job
    job_id = await create_job(
        config, user_id,
        name=f"Pulse: {task_id[:8]}",
        instruction=f"[PULSE:{task_id}:{template_id}]",
        cron_expression=every,
        job_type="cron",
        context=template_id,
    )
    return job_id


# ── Skills ─────────────────────────────────────────────────────────────


class SaveProgressTemplateSkill(BaseSkill):
    """Save (or auto-update) a progress check-in template."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "save_progress_template"

    @property
    def display_name(self) -> str:
        return "save progress template"

    @property
    def description(self) -> str:
        return (
            "Save a progress check-in template — questions + buttons + "
            "cadence — that drives pulse pings on tasks. Match by "
            "applies_to_category (e.g. 'code', 'writing') or leave null "
            "for the generic fallback. Examples: cron '*/30 * * * *' = "
            "every 30 minutes, '0 * * * *' = top of every hour."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name"},
                "every": {
                    "type": "string",
                    "description": "5-field cron, e.g. '*/30 * * * *' (every 30m)",
                },
                "applies_to_category": {
                    "type": "string",
                    "description": "Category match; null = generic fallback",
                },
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Questions to ask on each pulse",
                },
            },
            "required": ["name", "every"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.progress_templates import (
            create_template, _DEFAULT_BUTTONS,
        )
        try:
            tpl = await create_template(
                self._config, user_id,
                name=params["name"],
                every=params["every"],
                applies_to_category=params.get("applies_to_category"),
                questions=params.get("questions") or [],
                buttons=_DEFAULT_BUTTONS,
            )
        except ValueError as exc:
            return f"❌ Could not save template: {exc}"
        return f"✅ Saved template '{tpl['name']}' (every {tpl['every']})"


class ListProgressTemplatesSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_progress_templates"

    @property
    def display_name(self) -> str:
        return "list progress templates"

    @property
    def description(self) -> str:
        return "List all saved progress check-in templates with their cadence."

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.tasks.progress_templates import (
            ensure_default_templates, list_templates,
        )
        # First-touch seeding so the user always has the three defaults
        # available without an explicit setup step.
        await ensure_default_templates(self._config, user_id)
        templates = await list_templates(self._config, user_id)
        if not templates:
            return "(no progress templates yet)"
        lines: list[str] = []
        for t in templates:
            cat = t.get("applies_to_category") or "*"
            success = t.get("success_count") or 0
            runs = t.get("run_count") or 0
            auto = " (auto)" if t.get("auto_saved") else ""
            lines.append(
                f"• {t['name']}{auto} — every {t['every']} — "
                f"category={cat} — {success}/{runs} replies"
            )
        return "\n".join(lines)


class ApplyProgressTemplateSkill(BaseSkill):
    """Opt a task into pulse check-ins via a named or auto-resolved template."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "apply_progress_template"

    @property
    def display_name(self) -> str:
        return "apply progress template"

    @property
    def description(self) -> str:
        return (
            "Opt a task into pulse check-ins. Looks up the best-matching "
            "template by the task's category (or accepts an explicit "
            "template_name). Sets check_every on the task and creates "
            "the heartbeat agent_jobs row that fires the pulse on "
            "schedule. Pause anytime with pause_progress_pulse."
        )

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_name": {"type": "string", "description": "Task name (fuzzy match)"},
                "template_name": {
                    "type": "string",
                    "description": "Optional: explicit template name; else auto-resolve from category",
                },
            },
            "required": ["task_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.skills.builtin.task_manager import _fuzzy_match_task
        from lazyclaw.tasks.progress_templates import (
            ensure_default_templates, find_template_for_category, list_templates,
        )
        from lazyclaw.tasks.store import (
            append_progress_entry, list_tasks, update_task,
        )

        task_name = (params.get("task_name") or "").strip()
        explicit_template = (params.get("template_name") or "").strip()
        if not task_name:
            return "Task name is required."

        await ensure_default_templates(self._config, user_id)

        tasks = await list_tasks(self._config, user_id)
        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            return f"No task matching '{task_name}'."

        # Resolve template: explicit name first, else category-based
        # auto-pick.
        template = None
        if explicit_template:
            templates = await list_templates(self._config, user_id)
            for t in templates:
                if (t.get("name") or "").lower() == explicit_template.lower():
                    template = t
                    break
            if template is None:
                return f"No template named '{explicit_template}'."
        else:
            template = await find_template_for_category(
                self._config, user_id, match.get("category"),
            )
        if template is None:
            return (
                "No matching template found and no generic fallback. "
                "Save one with save_progress_template first."
            )

        # Pause any existing pulse job before starting a new one — keeps
        # one pulse cadence per task at a time.
        existing_template_id = match.get("progress_template_id")
        if existing_template_id:
            try:
                from lazyclaw.heartbeat.orchestrator import (
                    delete_job, list_jobs,
                )
                jobs = await list_jobs(self._config, user_id)
                stale_pulses = [
                    j for j in jobs
                    if (j.get("instruction") or "").startswith(
                        f"[PULSE:{match['id']}:"
                    )
                ]
                for j in stale_pulses:
                    await delete_job(self._config, user_id, j["id"])
            except Exception:
                logger.debug("could not clean stale pulse jobs", exc_info=True)

        job_id = await _create_pulse_job(
            self._config, user_id, match["id"],
            template["id"], template["every"],
        )

        await update_task(
            self._config, user_id, match["id"],
            check_every=template["every"],
            progress_template_id=template["id"],
        )

        # Record an "applied template" progress entry so the timeline
        # tells the story.
        await append_progress_entry(
            self._config, user_id, match["id"],
            kind="progress",
            text=f"Pulse on (template={template['name']}, every {template['every']})",
            source="auto",
        )

        return (
            f"⏰ Pulse on for '{match['title']}' — template '{template['name']}', "
            f"every {template['every']}. Tap a button on each ping or just "
            f"reply to log progress."
        )


class PauseProgressPulseSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "pause_progress_pulse"

    @property
    def display_name(self) -> str:
        return "pause progress pulse"

    @property
    def description(self) -> str:
        return "Pause check-in pulses on a task. Resume later with resume_progress_pulse."

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_name": {"type": "string", "description": "Task name (fuzzy)"},
            },
            "required": ["task_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.orchestrator import list_jobs, pause_job
        from lazyclaw.skills.builtin.task_manager import _fuzzy_match_task
        from lazyclaw.tasks.store import list_tasks

        task_name = (params.get("task_name") or "").strip()
        if not task_name:
            return "Task name is required."

        tasks = await list_tasks(self._config, user_id)
        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            return f"No task matching '{task_name}'."

        jobs = await list_jobs(self._config, user_id)
        prefix = f"[PULSE:{match['id']}:"
        paused = 0
        for j in jobs:
            if (j.get("instruction") or "").startswith(prefix):
                try:
                    await pause_job(self._config, user_id, j["id"])
                    paused += 1
                except Exception:
                    logger.debug("pause_job failed", exc_info=True)
        if paused == 0:
            return f"No active pulse on '{match['title']}'."
        return f"⏸️ Paused {paused} pulse(s) on '{match['title']}'."


class ResumeProgressPulseSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "resume_progress_pulse"

    @property
    def display_name(self) -> str:
        return "resume progress pulse"

    @property
    def description(self) -> str:
        return "Resume previously-paused check-in pulses on a task."

    @property
    def category(self) -> str:
        return "tasks"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_name": {"type": "string", "description": "Task name (fuzzy)"},
            },
            "required": ["task_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.heartbeat.orchestrator import list_jobs, resume_job
        from lazyclaw.skills.builtin.task_manager import _fuzzy_match_task
        from lazyclaw.tasks.store import list_tasks

        task_name = (params.get("task_name") or "").strip()
        if not task_name:
            return "Task name is required."

        tasks = await list_tasks(self._config, user_id)
        match = _fuzzy_match_task(tasks, task_name)
        if not match:
            return f"No task matching '{task_name}'."

        jobs = await list_jobs(self._config, user_id)
        prefix = f"[PULSE:{match['id']}:"
        resumed = 0
        for j in jobs:
            if (j.get("instruction") or "").startswith(prefix):
                try:
                    await resume_job(self._config, user_id, j["id"])
                    resumed += 1
                except Exception:
                    logger.debug("resume_job failed", exc_info=True)
        if resumed == 0:
            return f"No paused pulse on '{match['title']}'."
        return f"▶️ Resumed {resumed} pulse(s) on '{match['title']}'."
