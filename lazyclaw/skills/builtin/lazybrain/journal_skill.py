"""Daily-journal skills."""
from __future__ import annotations

from lazyclaw.lazybrain import events, journal
from lazyclaw.skills.base import BaseSkill


class AppendJournalSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_append_journal"

    @property
    def display_name(self) -> str:
        return "Append to journal"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def description(self) -> str:
        return (
            "Append a timestamped entry to the daily journal page. "
            "Use when the user says 'log this', 'add to today', or wants a "
            "diary-style entry. Creates the journal page if it doesn't exist."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD, 'today', or 'yesterday'. Defaults to today.",
                },
            },
            "required": ["content"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        note = await journal.append_journal(
            self._config,
            user_id,
            content=params["content"],
            iso_date=params.get("date"),
        )
        events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"], source="agent"
        )
        return f"📓 Journal updated: {note['title']} [{note['id'][:8]}]"


class ListJournalSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_list_journal"

    @property
    def display_name(self) -> str:
        return "List journal pages"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "List recent daily-journal pages, newest first."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 60,
                    "default": 14,
                }
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        notes = await journal.list_journal(
            self._config, user_id, limit=int(params.get("limit") or 14)
        )
        if not notes:
            return "(no journal entries yet — use lazybrain_append_journal to start)"
        lines = [f"Recent journal pages ({len(notes)}):"]
        for n in notes:
            lines.append(f"• {n['title']} [{n['id'][:8]}]")
        return "\n".join(lines)
