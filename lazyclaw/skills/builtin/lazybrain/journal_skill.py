"""Daily-journal skills."""
from __future__ import annotations

from lazyclaw.lazybrain import events, journal, store
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


class GetJournalSkill(BaseSkill):
    """Fetch the body of a specific day's journal page."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_get_journal"

    @property
    def display_name(self) -> str:
        return "Read journal"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Read the full content of a daily journal page. "
            "Accepts 'today', 'yesterday', or YYYY-MM-DD. Use before "
            "editing or deleting a journal entry so you know what's there."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD, 'today', or 'yesterday'. Defaults to today.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        date_arg = (params or {}).get("date")
        try:
            iso = journal.resolve_date(date_arg)
        except ValueError as exc:
            return f"Error: {exc}"
        note = await journal.get_journal(self._config, user_id, iso)
        if not note:
            return f"(no journal page for {iso})"
        body = (note.get("content") or "").strip()
        return (
            f"📓 {note.get('title') or f'Journal — {iso}'} [{note['id'][:8]}]\n\n"
            f"{body if body else '(empty)'}"
        )


class DeleteJournalSkill(BaseSkill):
    """Delete an entire day's journal page."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_delete_journal"

    @property
    def display_name(self) -> str:
        return "Delete journal page"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Delete the journal page for a given day. Accepts 'today', "
            "'yesterday', or YYYY-MM-DD. Use when the user says 'delete the "
            "journal', 'remove today's journal', or wants to clear a day."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD, 'today', or 'yesterday'. Defaults to today.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        date_arg = (params or {}).get("date")
        try:
            iso = journal.resolve_date(date_arg)
        except ValueError as exc:
            return f"Error: {exc}"
        note = await journal.get_journal(self._config, user_id, iso)
        if not note:
            return f"(no journal page for {iso} — nothing to delete)"
        deleted = await store.delete_note(self._config, user_id, note["id"])
        if not deleted:
            return f"Error: failed to delete journal {iso}."
        events.publish_note_deleted(user_id, note["id"], note.get("title"))
        return f"🗑️ Journal for {iso} deleted."


class DeleteJournalLineSkill(BaseSkill):
    """Remove matching bullet lines from a day's journal without touching the rest."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_delete_journal_line"

    @property
    def display_name(self) -> str:
        return "Delete journal line"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Remove bullet lines from a journal page that contain a given "
            "substring (case-insensitive). Surgical — leaves other entries "
            "intact. Use for 'delete the line about X from today's journal'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "match": {
                    "type": "string",
                    "description": "Substring that identifies the bullet(s) to remove.",
                },
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD, 'today', or 'yesterday'. Defaults to today.",
                },
            },
            "required": ["match"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        params = params or {}
        needle = (params.get("match") or "").strip()
        if not needle:
            return "Error: `match` is required."
        try:
            iso = journal.resolve_date(params.get("date"))
        except ValueError as exc:
            return f"Error: {exc}"

        note = await journal.get_journal(self._config, user_id, iso)
        if not note:
            return f"(no journal page for {iso})"

        body = note.get("content") or ""
        needle_lower = needle.lower()
        kept: list[str] = []
        removed = 0
        for line in body.splitlines():
            if line.lstrip().startswith("- ") and needle_lower in line.lower():
                removed += 1
                continue
            kept.append(line)

        if removed == 0:
            return f'No journal lines matched "{needle}" on {iso}.'

        new_body = "\n".join(kept).rstrip() + "\n"
        updated = await store.update_note(
            self._config, user_id, note["id"], content=new_body
        )
        if not updated:
            return f"Error: failed to update journal {iso}."
        events.publish_note_saved(
            user_id, note["id"], note.get("title") or "", note.get("tags") or [],
            source="agent",
        )
        return f"🗑️ Removed {removed} line(s) from journal {iso}."


class RewriteJournalSkill(BaseSkill):
    """Replace the body of a day's journal wholesale (heavy edit)."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_rewrite_journal"

    @property
    def display_name(self) -> str:
        return "Rewrite journal page"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Replace the entire body of a journal page. Use when the user "
            "asks to rewrite, reformat, or clean up a whole day at once. "
            "For small edits prefer lazybrain_delete_journal_line."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "New markdown body (will replace the existing one).",
                },
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD, 'today', or 'yesterday'. Defaults to today.",
                },
            },
            "required": ["content"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        params = params or {}
        content = params.get("content")
        if not content or not str(content).strip():
            return "Error: `content` is required and must be non-empty."
        try:
            iso = journal.resolve_date(params.get("date"))
        except ValueError as exc:
            return f"Error: {exc}"

        note = await journal.get_journal(self._config, user_id, iso)
        if not note:
            return (
                f"(no journal page for {iso} — use lazybrain_append_journal "
                f"to create one first)"
            )
        updated = await store.update_note(
            self._config, user_id, note["id"], content=str(content)
        )
        if not updated:
            return f"Error: failed to rewrite journal {iso}."
        events.publish_note_saved(
            user_id, note["id"], note.get("title") or "", note.get("tags") or [],
            source="agent",
        )
        return f"✏️ Journal for {iso} rewritten."
