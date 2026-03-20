"""Agent concurrency limit skills.

Four admin skills for controlling parallel specialist behavior:
set_max_agents, set_ram_limit, toggle_auto_delegate, show_agent_limits.
"""

from __future__ import annotations

import resource
import sys

from lazyclaw.runtime.agent_settings import get_agent_settings, update_agent_settings
from lazyclaw.skills.base import BaseSkill


class SetMaxAgentsSkill(BaseSkill):
    """Set the maximum number of parallel specialists."""

    name = "set_max_agents"
    display_name = "set max agents"
    description = "Set the maximum number of parallel specialists (1-10)."
    category = "admin"
    parameters_schema = {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "Max parallel specialists (1-10)",
            },
        },
        "required": ["count"],
    }

    def __init__(self, config=None):
        self._config = config

    async def execute(self, user_id: str, params: dict) -> str:
        count = params.get("count", 3)
        try:
            result = await update_agent_settings(
                self._config, user_id, {"max_concurrent_specialists": count},
            )
            return f"Max parallel specialists set to {result['max_concurrent_specialists']}."
        except ValueError as e:
            return f"Invalid value: {e}"


class SetRamLimitSkill(BaseSkill):
    """Set the RAM limit for agent operations."""

    name = "set_ram_limit"
    display_name = "set RAM limit"
    description = "Set the RAM limit in MB for agent operations (128-4096)."
    category = "admin"
    parameters_schema = {
        "type": "object",
        "properties": {
            "ram_mb": {
                "type": "integer",
                "description": "RAM limit in MB (128-4096)",
            },
        },
        "required": ["ram_mb"],
    }

    def __init__(self, config=None):
        self._config = config

    async def execute(self, user_id: str, params: dict) -> str:
        ram_mb = params.get("ram_mb", 512)
        try:
            result = await update_agent_settings(
                self._config, user_id, {"max_ram_mb": ram_mb},
            )
            return f"RAM limit set to {result['max_ram_mb']} MB."
        except ValueError as e:
            return f"Invalid value: {e}"


class ToggleAutoDelegateSkill(BaseSkill):
    """Enable or disable automatic delegation to specialists."""

    name = "toggle_auto_delegate"
    display_name = "toggle auto-delegate"
    description = "Enable or disable automatic delegation of heavy tasks to specialists."
    category = "admin"
    parameters_schema = {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "description": "true to enable, false to disable",
            },
        },
        "required": ["enabled"],
    }

    def __init__(self, config=None):
        self._config = config

    async def execute(self, user_id: str, params: dict) -> str:
        enabled = params.get("enabled", True)
        try:
            result = await update_agent_settings(
                self._config, user_id, {"auto_delegate": enabled},
            )
            state = "enabled" if result["auto_delegate"] else "disabled"
            return f"Auto-delegate {state}."
        except ValueError as e:
            return f"Invalid value: {e}"


class ShowAgentLimitsSkill(BaseSkill):
    """Show current agent concurrency settings and resource usage."""

    name = "show_agent_limits"
    display_name = "show agent limits"
    description = "Show current agent concurrency settings and resource usage."
    category = "admin"
    parameters_schema = {"type": "object", "properties": {}}

    def __init__(self, config=None):
        self._config = config

    async def execute(self, user_id: str, params: dict) -> str:
        settings = await get_agent_settings(self._config, user_id)

        usage = resource.getrusage(resource.RUSAGE_SELF)
        # macOS returns bytes, Linux returns KB
        ram_bytes = usage.ru_maxrss
        if sys.platform != "darwin":
            ram_bytes *= 1024
        ram_mb = ram_bytes / (1024 * 1024)

        lines = [
            "Agent Settings",
            f"  Auto-delegate: {'on' if settings['auto_delegate'] else 'off'}",
            f"  Max specialists: {settings['max_concurrent_specialists']}",
            f"  RAM limit: {settings['max_ram_mb']} MB (current: {ram_mb:.0f} MB)",
            f"  Specialist timeout: {settings['specialist_timeout_s']}s",
        ]
        return "\n".join(lines)
