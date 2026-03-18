"""Session management skills — history clearing and compression stats."""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class ClearHistorySkill(BaseSkill):
    """Clear conversation history for the current session."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "session"

    @property
    def name(self) -> str:
        return "clear_history"

    @property
    def description(self) -> str:
        return (
            "Clear all conversation history for the current session. "
            "This cannot be undone."
        )

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.db.connection import db_session

            async with db_session(self._config) as db:
                result = await db.execute(
                    "DELETE FROM agent_messages WHERE user_id = ?",
                    (user_id,),
                )
                await db.commit()
                deleted = result.rowcount
            return f"Cleared {deleted} messages from conversation history."
        except Exception as exc:
            return f"Error: {exc}"


class ShowCompressionSkill(BaseSkill):
    """Show context compression statistics."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "session"

    @property
    def name(self) -> str:
        return "show_compression"

    @property
    def description(self) -> str:
        return (
            "Show context compression statistics including summary counts, "
            "cache hit rate, and token savings."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.memory.compressor import get_compression_stats

            stats = await get_compression_stats(self._config, user_id)
            lines = [
                "Compression Statistics",
                "---------------------",
                f"Total messages:      {stats['total_messages']}",
                f"Active (in context): {stats['active_messages']}",
                f"Compressed:          {stats['compressed_messages']}",
                f"Summary count:       {stats['summary_count']}",
                f"Compression ratio:   {stats['compression_ratio']}%",
                f"Window size:         {stats['window_size']} messages",
            ]
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"
