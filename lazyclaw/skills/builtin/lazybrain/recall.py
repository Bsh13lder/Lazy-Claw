"""Recall skills — get one note, search many."""
from __future__ import annotations

from lazyclaw.lazybrain import store
from lazyclaw.skills.base import BaseSkill


def _snippet(note: dict, width: int = 200) -> str:
    body = (note.get("content") or "").strip()
    if len(body) <= width:
        return body
    return body[: width - 1] + "…"


class GetNoteSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_get_note"

    @property
    def display_name(self) -> str:
        return "Get note"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "Fetch a single note by ID. Returns title, content, tags, backlinks count."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        note_id = params["note_id"]
        note = await store.get_note(self._config, user_id, note_id)
        if not note:
            return f"❌ Note not found: {note_id}"
        backlinks = await store.get_backlinks(
            self._config, user_id, note["title_key"] or note_id
        )
        tags = " ".join(f"#{t}" for t in note.get("tags") or []) or "(no tags)"
        return (
            f"📝 {note['title'] or '(untitled)'}\n"
            f"ID: {note['id']}\n"
            f"Tags: {tags}\n"
            f"Importance: {note.get('importance', 5)}/10"
            f"{' · 📌 pinned' if note.get('pinned') else ''}\n"
            f"Backlinks: {len(backlinks)}\n\n"
            f"{note['content']}"
        )


class SearchNotesSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_search_notes"

    @property
    def display_name(self) -> str:
        return "Search notes"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Search the user's second brain by substring. Optional tag filter. "
            "Returns top matches with ID + title + snippet."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "tag": {
                    "type": "string",
                    "description": "Optional tag filter, without the leading '#'.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        hits = await store.search_notes(
            self._config,
            user_id,
            params["query"],
            tag=params.get("tag"),
            limit=int(params.get("limit") or 10),
        )
        if not hits:
            return "(no matches)"
        lines = [f"Found {len(hits)} note(s):"]
        for n in hits:
            lines.append(
                f"• {n['title'] or '(untitled)'} "
                f"[{n['id'][:8]}] — {_snippet(n, 120)}"
            )
        return "\n".join(lines)
