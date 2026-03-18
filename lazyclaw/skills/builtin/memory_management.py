"""Memory management skills — list, view, and delete personal memories and daily logs."""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class ListMemoriesSkill(BaseSkill):
    """List all stored personal memories."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "list_memories"

    @property
    def description(self) -> str:
        return (
            "List all stored personal memories about the user, "
            "showing type and when they were saved."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.memory.personal import get_memories

            memories = await get_memories(self._config, user_id)
            if not memories:
                return "No memories stored yet."

            lines = [f"Personal memories ({len(memories)}):"]
            for mem in memories:
                lines.append(
                    f"  - [{mem['memory_type']}] {mem['content'][:80]} "
                    f"(id: {mem['id']}, saved: {mem['created_at']})"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


class DeleteMemorySkill(BaseSkill):
    """Delete a specific personal memory by ID."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "delete_memory"

    @property
    def description(self) -> str:
        return "Delete a specific personal memory by its ID."

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to delete",
                },
            },
            "required": ["memory_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.memory.personal import delete_memory

            result = await delete_memory(
                self._config, user_id, params["memory_id"]
            )
            if result:
                return f"Memory {params['memory_id']} deleted."
            return f"Memory {params['memory_id']} not found."
        except Exception as exc:
            return f"Error: {exc}"


class ListDailyLogsSkill(BaseSkill):
    """List recent daily log summaries."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "list_daily_logs"

    @property
    def description(self) -> str:
        return "List recent daily log summaries showing dates and message counts."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max logs to show (default 10)",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.memory.daily_log import list_daily_logs

            limit = params.get("limit", 10)
            logs = await list_daily_logs(self._config, user_id, limit=limit)
            if not logs:
                return "No daily logs found."

            lines = [f"Daily logs ({len(logs)}):"]
            for log in logs:
                lines.append(
                    f"  - {log['date']}: {log['summary'][:60]} "
                    f"({log['message_count']} messages)"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


class ViewDailyLogSkill(BaseSkill):
    """View the full daily log for a specific date."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "view_daily_log"

    @property
    def description(self) -> str:
        return "View the full daily log for a specific date (YYYY-MM-DD format)."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format",
                },
            },
            "required": ["date"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.memory.daily_log import get_daily_log

            log = await get_daily_log(self._config, user_id, params["date"])
            if not log:
                return f"No daily log found for {params['date']}."

            return (
                f"Daily log for {log['date']}:\n"
                f"Messages: {log['message_count']}\n"
                f"Created: {log['created_at']}\n\n"
                f"{log['summary']}"
            )
        except Exception as exc:
            return f"Error: {exc}"


class DeleteDailyLogSkill(BaseSkill):
    """Delete the daily log for a specific date."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "memory"

    @property
    def name(self) -> str:
        return "delete_daily_log"

    @property
    def description(self) -> str:
        return "Delete the daily log for a specific date."

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format",
                },
            },
            "required": ["date"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.memory.daily_log import delete_daily_log

            result = await delete_daily_log(
                self._config, user_id, params["date"]
            )
            if result:
                return f"Daily log for {params['date']} deleted."
            return f"No daily log found for {params['date']}."
        except Exception as exc:
            return f"Error: {exc}"
