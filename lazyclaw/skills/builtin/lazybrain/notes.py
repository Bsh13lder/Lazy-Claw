"""Note create / update / delete skills."""
from __future__ import annotations

from lazyclaw.lazybrain import events, store
from lazyclaw.skills.base import BaseSkill


def _format_note(note: dict) -> str:
    tag_line = " ".join(f"#{t}" for t in note.get("tags") or [])
    header = note["title"] or "(untitled)"
    pins = " 📌" if note.get("pinned") else ""
    tag_block = f"\n{tag_line}" if tag_line else ""
    return (
        f"✅ Saved: {header}{pins}\n"
        f"ID: {note['id']}{tag_block}"
    )


class SaveNoteSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_save_note"

    @property
    def display_name(self) -> str:
        return "Save note"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def description(self) -> str:
        return (
            "Save a note to the user's second brain. Supports markdown, "
            "[[wikilinks]] to other notes, and #tags for filtering. "
            "Use this whenever the user asks to remember an idea, a fact, "
            "a recipe, a decision, or a link. Tag #journal/YYYY-MM-DD for "
            "diary entries."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Markdown body. Can include [[wikilinks]] and #tags.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional title. Derived from first line if omitted.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit tags in addition to #hashtags in content.",
                },
                "importance": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "1–10. Notes >= 8 surface as pinned in the context briefing.",
                },
                "pinned": {
                    "type": "boolean",
                    "description": "Pin to the top of the timeline and inject into agent context.",
                },
            },
            "required": ["content"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        # Stamp agent-origin so the UI can filter "who wrote this".
        tags = list(params.get("tags") or [])
        if not any(t.startswith("owner/") for t in tags):
            tags.append("owner/agent")
        note = await store.save_note(
            self._config,
            user_id,
            content=params["content"],
            title=params.get("title"),
            tags=tags,
            importance=int(params.get("importance") or 5),
            pinned=bool(params.get("pinned") or False),
        )
        events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"], source="agent"
        )
        return _format_note(note)


class UpdateNoteSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_update_note"

    @property
    def display_name(self) -> str:
        return "Update note"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def description(self) -> str:
        return (
            "Update an existing note's content, title, tags, or importance. "
            "Pass only the fields you want to change."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "note_id": {"type": "string"},
                "content": {"type": "string"},
                "title": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "importance": {"type": "integer", "minimum": 1, "maximum": 10},
                "pinned": {"type": "boolean"},
            },
            "required": ["note_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        note_id = params["note_id"]
        note = await store.update_note(
            self._config,
            user_id,
            note_id,
            content=params.get("content"),
            title=params.get("title"),
            tags=params.get("tags"),
            importance=params.get("importance"),
            pinned=params.get("pinned"),
        )
        if not note:
            return f"❌ Note not found: {note_id}"
        events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"], source="agent"
        )
        return _format_note(note)


class DeleteNoteSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_delete_note"

    @property
    def display_name(self) -> str:
        return "Delete note"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return "Delete a note from the second brain. Ask the user to confirm first."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "note_id": {"type": "string"},
            },
            "required": ["note_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        note_id = params["note_id"]
        note = await store.get_note(self._config, user_id, note_id)
        ok = await store.delete_note(self._config, user_id, note_id)
        if not ok:
            return f"❌ Note not found: {note_id}"
        events.publish_note_deleted(
            user_id, note_id, note["title"] if note else None
        )
        return f"🗑️ Deleted: {note['title'] if note else note_id}"
