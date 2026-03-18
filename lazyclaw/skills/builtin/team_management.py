"""Team management skills — view/update team settings, manage specialists.

Provides agent-accessible tools for configuring multi-agent team mode,
critic settings, and custom specialist definitions.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Map user-friendly mode names to internal values
_MODE_MAP = {"off": "never", "on": "always", "auto": "auto"}
_MODE_DISPLAY = {"never": "off", "always": "on", "auto": "auto"}


def _display_mode(internal: str) -> str:
    """Convert internal mode value to user-friendly display."""
    return _MODE_DISPLAY.get(internal, internal)


def _internal_mode(user_mode: str) -> str:
    """Convert user-friendly mode to internal value."""
    return _MODE_MAP.get(user_mode, user_mode)


def _format_specialist(spec) -> str:
    """Format a single specialist config as readable text."""
    builtin_tag = " (built-in)" if spec.is_builtin else " (custom)"
    model = spec.preferred_model or "default"
    skills = ", ".join(spec.allowed_skills) if spec.allowed_skills else "none"
    return (
        f"  {spec.display_name}{builtin_tag}\n"
        f"    Name: {spec.name}\n"
        f"    Model: {model}\n"
        f"    Skills: {skills}"
    )


class ShowTeamSettingsSkill(BaseSkill):
    """Show multi-agent team configuration."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "teams"

    @property
    def name(self) -> str:
        return "show_team_settings"

    @property
    def description(self) -> str:
        return (
            "Show multi-agent team configuration including mode, critic "
            "settings, and available specialists."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.teams.settings import get_team_settings
            from lazyclaw.teams.specialist import load_specialists

            settings = await get_team_settings(self._config, user_id)
            specialists = await load_specialists(self._config, user_id)

            mode = _display_mode(settings.get("mode", "never"))
            critic = _display_mode(settings.get("critic_mode", "auto"))
            max_p = settings.get("max_parallel", 3)
            timeout = settings.get("specialist_timeout", 120)

            lines = [
                "Team Settings",
                "=============",
                f"Team mode:            {mode}",
                f"Critic mode:          {critic}",
                f"Max parallel:         {max_p}",
                f"Specialist timeout:   {timeout}s",
                "",
                f"Specialists ({len(specialists)}):",
                "-" * 40,
            ]
            for spec in specialists:
                lines.append(_format_specialist(spec))
                lines.append("")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error loading team settings: {exc}"


class SetTeamModeSkill(BaseSkill):
    """Set the multi-agent team mode."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "teams"

    @property
    def name(self) -> str:
        return "set_team_mode"

    @property
    def description(self) -> str:
        return (
            "Set the multi-agent team mode. 'off' disables teams, 'on' always "
            "uses teams, 'auto' lets the agent decide."
        )

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["off", "on", "auto"],
                    "description": "Team mode: off, on, or auto",
                },
            },
            "required": ["mode"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.teams.settings import update_team_settings

            internal = _internal_mode(params["mode"])
            updated = await update_team_settings(
                self._config, user_id, {"mode": internal}
            )
            display = _display_mode(updated.get("mode", internal))
            return f"Team mode set to '{display}'."
        except Exception as exc:
            return f"Error setting team mode: {exc}"


class SetCriticModeSkill(BaseSkill):
    """Set the critic mode for team responses."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "teams"

    @property
    def name(self) -> str:
        return "set_critic_mode"

    @property
    def description(self) -> str:
        return (
            "Set the critic mode for team responses. 'off' skips review, "
            "'on' always reviews, 'auto' reviews when 2+ specialists respond."
        )

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["off", "on", "auto"],
                    "description": "Critic mode: off, on, or auto",
                },
            },
            "required": ["mode"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.teams.settings import update_team_settings

            internal = _internal_mode(params["mode"])
            updated = await update_team_settings(
                self._config, user_id, {"critic_mode": internal}
            )
            display = _display_mode(updated.get("critic_mode", internal))
            return f"Critic mode set to '{display}'."
        except Exception as exc:
            return f"Error setting critic mode: {exc}"


class ListSpecialistsSkill(BaseSkill):
    """List all available agent specialists."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "teams"

    @property
    def name(self) -> str:
        return "list_specialists"

    @property
    def description(self) -> str:
        return (
            "List all available agent specialists (built-in and custom) "
            "with their skills and models."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.teams.specialist import load_specialists

            specialists = await load_specialists(self._config, user_id)
            if not specialists:
                return "No specialists configured."

            lines = [f"Specialists ({len(specialists)}):", ""]
            for spec in specialists:
                lines.append(_format_specialist(spec))
                lines.append("")

            return "\n".join(lines)
        except Exception as exc:
            return f"Error listing specialists: {exc}"


class ManageSpecialistSkill(BaseSkill):
    """Create, update, or delete a custom agent specialist."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return "teams"

    @property
    def name(self) -> str:
        return "manage_specialist"

    @property
    def description(self) -> str:
        return "Create, update, or delete a custom agent specialist."

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "delete"],
                    "description": "Action to perform",
                },
                "name": {
                    "type": "string",
                    "description": "Specialist identifier (snake_case)",
                },
                "display_name": {
                    "type": "string",
                    "description": "Human-readable name for the specialist",
                },
                "system_prompt": {
                    "type": "string",
                    "description": "System prompt defining the specialist's behavior",
                },
                "allowed_skills": {
                    "type": "string",
                    "description": "Comma-separated list of skill names the specialist can use",
                },
                "preferred_model": {
                    "type": "string",
                    "description": "Optional model override for this specialist",
                },
            },
            "required": ["action", "name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            action = params["action"]
            spec_name = params["name"]

            if action == "delete":
                from lazyclaw.teams.specialist import delete_specialist

                deleted = await delete_specialist(
                    self._config, user_id, spec_name
                )
                if deleted:
                    return f"Deleted specialist '{spec_name}'."
                return f"Error: Specialist '{spec_name}' not found or is built-in."

            if action in ("create", "update"):
                from lazyclaw.teams.specialist import (
                    SpecialistConfig,
                    save_specialist,
                )

                display_name = params.get("display_name", spec_name)
                system_prompt = params.get("system_prompt", "")
                if not system_prompt:
                    return "Error: 'system_prompt' is required for create/update."

                raw_skills = params.get("allowed_skills", "")
                skills = tuple(
                    s.strip() for s in raw_skills.split(",") if s.strip()
                )

                specialist = SpecialistConfig(
                    name=spec_name,
                    display_name=display_name,
                    system_prompt=system_prompt,
                    allowed_skills=skills,
                    preferred_model=params.get("preferred_model"),
                    is_builtin=False,
                )

                record_id = await save_specialist(
                    self._config, user_id, specialist
                )
                verb = "Created" if action == "create" else "Updated"
                skills_display = ", ".join(skills) if skills else "none"
                return (
                    f"{verb} specialist '{display_name}' ({spec_name}).\n"
                    f"Skills: {skills_display}\n"
                    f"Record ID: {record_id}"
                )

            return f"Error: Unknown action '{action}'. Use create, update, or delete."
        except Exception as exc:
            return f"Error managing specialist: {exc}"
