"""Browser template NL skills — save and run reusable browsing recipes.

Templates are the "saved-agent" pattern from taskbot, ported into LazyClaw.
Common use case is government appointments where the same multi-step flow
runs many times (DGT cita previa, NIE renewal, doctor booking, etc.).

The agent calls:
  - save_browser_template — capture a recipe (playbook + setup_urls + checkpoints)
  - list_browser_templates — show what's saved
  - delete_browser_template — remove a recipe
  - run_browser_template — load a template and run it now
  - watch_appointment_slots — hook a template to a watcher (zero-token slot polling)
"""

from __future__ import annotations

import json
import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SaveBrowserTemplateSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def name(self) -> str:
        return "save_browser_template"

    @property
    def description(self) -> str:
        return (
            "Save a reusable browser flow (playbook + setup URLs + approval checkpoints) "
            "so the agent can repeat it later by name. "
            "Use for government appointments (DGT, cita previa, NIE), recurring bookings "
            "(doctor, gym, restaurant), or any multi-step browsing flow you want to repeat. "
            "After saving, the agent can run it via run_browser_template."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short unique name e.g. 'DGT cita previa'"},
                "playbook": {
                    "type": "string",
                    "description": (
                        "Free-form instructions for how to run the flow. "
                        "Mention required vault keys (e.g. 'use NIE from vault'), "
                        "site quirks, what to look for, what to skip."
                    ),
                },
                "setup_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URLs to open before the flow starts.",
                },
                "checkpoints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Names of approval checkpoints (must match request_user_approval names). "
                        "E.g. ['Pick date', 'Confirm booking']"
                    ),
                },
                "icon": {"type": "string", "description": "Optional emoji or short icon."},
                "watch_url": {
                    "type": "string",
                    "description": (
                        "Optional URL the slot-watcher will hit when watch_appointment_slots is enabled."
                    ),
                },
                "watch_extractor": {
                    "type": "string",
                    "description": (
                        "Optional JS string for cheap polling. Must return a value that "
                        "changes when slots become available."
                    ),
                },
                "watch_condition": {
                    "type": "string",
                    "description": "Optional human description of the trigger condition.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: not configured"
        from lazyclaw.browser import templates as tpl_store

        try:
            tpl = await tpl_store.create_template(
                self._config, user_id,
                name=params["name"],
                icon=params.get("icon"),
                playbook=params.get("playbook"),
                setup_urls=params.get("setup_urls"),
                checkpoints=params.get("checkpoints"),
                watch_url=params.get("watch_url"),
                watch_extractor=params.get("watch_extractor"),
                watch_condition=params.get("watch_condition"),
            )
        except Exception as exc:
            return f"Could not save template: {exc}"
        return f"Saved template '{tpl['name']}' (id: {tpl['id'][:8]})."


class ListBrowserTemplatesSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def name(self) -> str:
        return "list_browser_templates"

    @property
    def description(self) -> str:
        return (
            "List saved browser templates (recipes) the agent can run by name. "
            "Use when the user asks 'what flows do you have', "
            "'show my templates', 'list my saved bots'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: not configured"
        from lazyclaw.browser import templates as tpl_store

        items = await tpl_store.list_templates(self._config, user_id)
        if not items:
            return (
                "No saved browser templates yet. "
                "Use save_browser_template to capture a flow you want to repeat."
            )
        lines = ["Saved browser templates:"]
        for t in items:
            icon = t.get("icon") or "🌐"
            extras: list[str] = []
            if t.get("watch_url"):
                extras.append("slot-watch")
            if t.get("checkpoints"):
                extras.append(f"{len(t['checkpoints'])} checkpoint(s)")
            tail = f" [{', '.join(extras)}]" if extras else ""
            lines.append(f"  {icon} {t['name']}{tail}")
        return "\n".join(lines)


class DeleteBrowserTemplateSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def name(self) -> str:
        return "delete_browser_template"

    @property
    def description(self) -> str:
        return "Delete a saved browser template by exact name."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact template name."},
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: not configured"
        from lazyclaw.browser import templates as tpl_store

        tpl = await tpl_store.get_template_by_name(self._config, user_id, params["name"])
        if tpl is None:
            return f"No template named '{params['name']}'."
        ok = await tpl_store.delete_template(self._config, user_id, tpl["id"])
        return "Deleted." if ok else "Could not delete."


class RunBrowserTemplateSkill(BaseSkill):
    """Load a saved template by name and hand off the hydrated instruction.

    The skill returns the playbook + setup URLs + checkpoint reminder. The
    agent reads the result and continues its TAOR loop using its existing
    browser tools — no new infrastructure required.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def name(self) -> str:
        return "run_browser_template"

    @property
    def description(self) -> str:
        return (
            "Load a saved browser template and follow it now. "
            "Use when the user says 'run my DGT bot', 'do the cita previa', "
            "'book my appointment using template X', or names a saved flow. "
            "Always pair this with the existing browser skill — this only "
            "loads the recipe; the browser skill executes the steps."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name to run."},
                "input": {
                    "type": "string",
                    "description": (
                        "Optional extra context the user added "
                        "('renew NIE Madrid', 'tomorrow morning slot', etc)."
                    ),
                },
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: not configured"
        from lazyclaw.browser import templates as tpl_store

        tpl = await tpl_store.get_template_by_name(
            self._config, user_id, params["name"],
        )
        if tpl is None:
            return f"No template named '{params['name']}'. Use list_browser_templates to see what is saved."

        instruction = tpl_store.build_run_instruction(tpl, params.get("input"))
        return (
            f"Loaded template '{tpl['name']}'. Follow this plan now using the browser skill:\n\n"
            f"{instruction}"
        )


class WatchAppointmentSlotsSkill(BaseSkill):
    """Wire a template's watch_url/extractor to the existing watcher daemon.

    Reuses the existing agent_jobs + heartbeat watcher path so we get
    zero-token polling, Telegram push, and now BrowserCanvas alerts for free.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser_management"

    @property
    def name(self) -> str:
        return "watch_appointment_slots"

    @property
    def description(self) -> str:
        return (
            "Start zero-token slot polling for a saved template. "
            "Hits the template's watch_url at a fixed interval, runs the template's "
            "JS extractor, and notifies via Telegram + BrowserCanvas when the trigger "
            "condition is met. Use for 'watch DGT for slots', "
            "'tell me when an appointment opens', 'monitor cita previa'. "
            "The template must have watch_url and watch_extractor configured."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "template_name": {"type": "string"},
                "interval_minutes": {
                    "type": "integer",
                    "description": "How often to poll. Default 15.",
                },
                "duration_hours": {
                    "type": "number",
                    "description": "How long to keep watching. Default 168 (7 days).",
                },
            },
            "required": ["template_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: not configured"
        from datetime import datetime, timedelta, timezone

        from lazyclaw.browser import templates as tpl_store
        from lazyclaw.heartbeat.orchestrator import create_job, update_job

        tpl = await tpl_store.get_template_by_name(
            self._config, user_id, params["template_name"],
        )
        if tpl is None:
            return f"No template named '{params['template_name']}'."
        if not tpl.get("watch_url"):
            return (
                f"Template '{tpl['name']}' has no watch_url configured. "
                "Update it via save_browser_template (re-save with watch_url + watch_extractor)."
            )

        interval = max(1, int(params.get("interval_minutes") or 15)) * 60
        duration = float(params.get("duration_hours") or 168.0)
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=duration)
        ).isoformat() if duration > 0 else None

        # Build watcher context (matches lazyclaw/browser/watcher.py shape)
        ctx = {
            "url": tpl["watch_url"],
            "what_to_watch": tpl.get("watch_condition") or f"slots for {tpl['name']}",
            "custom_js": tpl.get("watch_extractor"),
            "interval": interval,
            "last_check": None,
            "last_value": None,
            "expires_at": expires,
            "one_shot": False,
            "template_id": tpl["id"],
            "template_name": tpl["name"],
        }

        job_id = await create_job(
            config=self._config,
            user_id=user_id,
            name=f"watch:{tpl['name']}",
            instruction=f"Slot watcher for template {tpl['name']}",
            cron_expression=None,
            job_type="watcher",
            context=json.dumps(ctx),
        )

        # Remember which job handles this template
        await tpl_store.update_template(
            self._config, user_id, tpl["id"], watch_job_id=job_id,
        )

        return (
            f"Watching {tpl['watch_url']} for {tpl['name']} "
            f"every {interval // 60} min. I'll ping you when slots open."
        )
