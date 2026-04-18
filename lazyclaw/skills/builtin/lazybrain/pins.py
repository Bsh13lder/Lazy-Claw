"""Pin / unpin / list-pinned skills — top-pinned notes get injected into
the agent's context, so pins act like a user-editable priority list."""
from __future__ import annotations

from lazyclaw.lazybrain import store
from lazyclaw.skills.base import BaseSkill


class PinNoteSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_pin_note"

    @property
    def display_name(self) -> str:
        return "Pin note"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def description(self) -> str:
        return (
            "Pin a note so it surfaces in the top of the timeline and is "
            "injected into the agent's system prompt as pinned context."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        ok = await store.set_pinned(
            self._config, user_id, params["note_id"], True
        )
        return "📌 Pinned." if ok else f"❌ Note not found: {params['note_id']}"


class UnpinNoteSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_unpin_note"

    @property
    def display_name(self) -> str:
        return "Unpin note"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def description(self) -> str:
        return "Unpin a previously-pinned note."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        ok = await store.set_pinned(
            self._config, user_id, params["note_id"], False
        )
        return "📎 Unpinned." if ok else f"❌ Note not found: {params['note_id']}"


class ListPinnedSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_list_pinned"

    @property
    def display_name(self) -> str:
        return "List pinned notes"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "List the user's pinned notes (priority-ordered)."

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, user_id: str, params: dict) -> str:
        notes = await store.list_notes(
            self._config, user_id, pinned_only=True, limit=30
        )
        if not notes:
            return "(no pinned notes)"
        lines = [f"📌 Pinned notes ({len(notes)}):"]
        for n in notes:
            tags = " ".join(f"#{t}" for t in n.get("tags") or [])
            lines.append(
                f"• {n['title'] or '(untitled)'} [{n['id'][:8]}]"
                + (f"  {tags}" if tags else "")
            )
        return "\n".join(lines)
