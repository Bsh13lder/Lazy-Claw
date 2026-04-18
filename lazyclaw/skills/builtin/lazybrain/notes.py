"""Note create / update / delete skills."""
from __future__ import annotations

from lazyclaw.lazybrain import events, store, wikilinks
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


class ListTagsSkill(BaseSkill):
    """Discover the tags in use across the second brain."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_list_tags"

    @property
    def display_name(self) -> str:
        return "List tags"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "List all tags used across the user's second brain, with a count "
            "of how many notes use each. Use this to discover what tags exist "
            "before searching or filtering."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 60,
                    "description": "Max tags to return, by count desc. Default 60.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        limit = int((params or {}).get("limit") or 60)
        rows = await store.list_tags(self._config, user_id)
        if not rows:
            return "(no tags yet — tag a note with #hashtags to start)"
        shown = rows[:limit]
        lines = [f"Tags in use ({len(shown)} of {len(rows)}):"]
        for row in shown:
            lines.append(f"  #{row['tag']} ({row['count']})")
        return "\n".join(lines)


class ListTitlesSkill(BaseSkill):
    """Browse every page title in the second brain (plaintext keys, no decrypt)."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_list_titles"

    @property
    def display_name(self) -> str:
        return "List page titles"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "List every page title in the user's second brain (most recently "
            "updated first). Titles come back normalised (lowercase, "
            "whitespace collapsed) — good enough to resolve via "
            "lazybrain_get_note / lazybrain_search_notes."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "default": 200,
                    "description": "Max titles to return. Default 200.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        limit = int((params or {}).get("limit") or 200)
        titles = await store.list_titles(self._config, user_id, limit=limit)
        if not titles:
            return "(no pages yet — save a note to start your second brain)"
        lines = [f"Pages ({len(titles)}):"]
        for t in titles:
            lines.append(f"  • {t}")
        return "\n".join(lines)


class RenamePageSkill(BaseSkill):
    """Rename a note's title and rewrite every ``[[old title]]`` wikilink."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_rename_page"

    @property
    def display_name(self) -> str:
        return "Rename page"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Rename a note/page. Updates the title AND rewrites every "
            "[[old title]] wikilink across all other notes so backlinks don't "
            "break. If a page with `new_title` already exists, use "
            "lazybrain_merge_notes instead."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "old_title": {
                    "type": "string",
                    "description": "Current title (case-insensitive match).",
                },
                "new_title": {
                    "type": "string",
                    "description": "Replacement title — used verbatim.",
                },
            },
            "required": ["old_title", "new_title"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        params = params or {}
        old_title = (params.get("old_title") or "").strip()
        new_title = (params.get("new_title") or "").strip()
        if not old_title or not new_title:
            return "Error: `old_title` and `new_title` are both required."
        if wikilinks.normalize_page(old_title) == wikilinks.normalize_page(
            new_title
        ):
            return "Error: new_title is the same as old_title."

        target = await store.find_by_title(self._config, user_id, old_title)
        if not target:
            return f'❌ No page titled "{old_title}".'

        clash = await store.find_by_title(self._config, user_id, new_title)
        if clash and clash["id"] != target["id"]:
            return (
                f'❌ A page titled "{new_title}" already exists (id '
                f'{clash["id"][:8]}). Use lazybrain_merge_notes instead.'
            )

        # 1. Rewrite wikilinks in every note that currently links to the old title
        backlinks = await store.get_backlinks(self._config, user_id, old_title)
        rewrites = 0
        for note in backlinks:
            new_content, count = wikilinks.rewrite_wikilink_target(
                note.get("content") or "", old_title, new_title
            )
            if count == 0:
                continue
            updated = await store.update_note(
                self._config, user_id, note["id"], content=new_content
            )
            if updated:
                rewrites += count
                events.publish_note_saved(
                    user_id,
                    updated["id"],
                    updated["title"],
                    updated["tags"],
                    source="agent",
                )

        # 2. Rename the target page itself (title_key + backlink pointers handled inside store)
        renamed = await store.update_note(
            self._config, user_id, target["id"], title=new_title
        )
        if not renamed:
            return f"❌ Failed to rename page (id {target['id'][:8]})."
        events.publish_note_saved(
            user_id,
            renamed["id"],
            renamed["title"],
            renamed["tags"],
            source="agent",
        )

        return (
            f'✏️ Renamed "{old_title}" → "{new_title}" '
            f"(id {renamed['id'][:8]}, rewrote {rewrites} wikilink(s) "
            f"in {len(backlinks)} note(s))."
        )


class MergeNotesSkill(BaseSkill):
    """Merge one note into another and rewrite all [[source]] wikilinks."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_merge_notes"

    @property
    def display_name(self) -> str:
        return "Merge notes"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def description(self) -> str:
        return (
            "Merge note `merge` into note `keep`: appends the merge body, "
            "unions tags, rewrites every [[merge_title]] wikilink across the "
            "brain to [[keep_title]], then deletes the merged note. Use for "
            "dedup when two notes describe the same thing. Both args accept "
            "either a note id or a title."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keep": {
                    "type": "string",
                    "description": "Note to keep — id or title.",
                },
                "merge": {
                    "type": "string",
                    "description": "Note to fold into `keep` — id or title.",
                },
                "separator": {
                    "type": "string",
                    "description": "Inserted between the two bodies. Default `\\n\\n---\\n\\n`.",
                },
            },
            "required": ["keep", "merge"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        params = params or {}
        keep_ref = (params.get("keep") or "").strip()
        merge_ref = (params.get("merge") or "").strip()
        sep = params.get("separator") or "\n\n---\n\n"
        if not keep_ref or not merge_ref:
            return "Error: `keep` and `merge` are both required."

        keep = await _resolve_note(self._config, user_id, keep_ref)
        merge = await _resolve_note(self._config, user_id, merge_ref)
        if not keep:
            return f'❌ Could not resolve `keep` = "{keep_ref}".'
        if not merge:
            return f'❌ Could not resolve `merge` = "{merge_ref}".'
        if keep["id"] == merge["id"]:
            return "Error: `keep` and `merge` are the same note."

        # 1. Merge content + tags into `keep`
        merged_content = (keep.get("content") or "").rstrip() + sep + (
            merge.get("content") or ""
        ).lstrip()
        seen: set[str] = set()
        merged_tags: list[str] = []
        for t in list(keep.get("tags") or []) + list(merge.get("tags") or []):
            if t not in seen:
                seen.add(t)
                merged_tags.append(t)
        updated_keep = await store.update_note(
            self._config,
            user_id,
            keep["id"],
            content=merged_content,
            tags=merged_tags,
        )
        if not updated_keep:
            return f"❌ Failed to update keep note (id {keep['id'][:8]})."
        events.publish_note_saved(
            user_id,
            updated_keep["id"],
            updated_keep["title"],
            updated_keep["tags"],
            source="agent",
        )

        # 2. Rewrite every [[merge_title]] → [[keep_title]] in the rest of the brain
        rewrites = 0
        affected = 0
        merge_title = merge.get("title") or ""
        keep_title = updated_keep.get("title") or keep.get("title") or ""
        if merge_title and keep_title:
            backlinks = await store.get_backlinks(
                self._config, user_id, merge_title
            )
            for note in backlinks:
                if note["id"] == merge["id"] or note["id"] == keep["id"]:
                    continue
                new_content, count = wikilinks.rewrite_wikilink_target(
                    note.get("content") or "", merge_title, keep_title
                )
                if count == 0:
                    continue
                updated = await store.update_note(
                    self._config, user_id, note["id"], content=new_content
                )
                if updated:
                    rewrites += count
                    affected += 1
                    events.publish_note_saved(
                        user_id,
                        updated["id"],
                        updated["title"],
                        updated["tags"],
                        source="agent",
                    )

        # 3. Delete the merged note (cascade handles outbound links)
        await store.delete_note(self._config, user_id, merge["id"])
        events.publish_note_deleted(user_id, merge["id"], merge_title or None)

        return (
            f"🔀 Merged \"{merge_title or merge['id'][:8]}\" into "
            f"\"{keep_title or keep['id'][:8]}\" "
            f"(rewrote {rewrites} wikilink(s) in {affected} note(s))."
        )


async def _resolve_note(config, user_id: str, ref: str) -> dict | None:
    """Resolve a user-supplied reference that can be a note id or a title."""
    note = await store.get_note(config, user_id, ref)
    if note:
        return note
    return await store.find_by_title(config, user_id, ref)
