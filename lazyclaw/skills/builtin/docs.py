"""Agent skills for private encrypted documents.

NL control over :mod:`lazyclaw.docs.store`. The agent creates docs, reads the
plain text, appends paragraphs, replaces the whole body, and exports/sends —
all over the same encrypted Univer ``IDocumentData`` snapshot the web editor
uses. Channel-agnostic: identical behaviour in Telegram, Web UI chat, and CLI.

A doc is identified by id, exact name, or substring (see
:func:`_resolve_doc_id`); omit the reference to act on the most recently
edited doc.
"""

from __future__ import annotations

import logging
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_MAX_PREVIEW_CHARS = 4000


async def _resolve_doc_id(
    config: Any, user_id: str, ref: str | None
) -> tuple[str | None, str | None]:
    """Resolve a doc reference (id, exact name, or substring) to an id.

    Returns ``(doc_id, None)`` on success or ``(None, error_message)``.
    With no ref, picks the most recently updated doc.
    """
    from lazyclaw.docs.store import list_docs

    rows = await list_docs(config, user_id)
    if not rows:
        return None, "You have no docs yet — create one first."
    if not ref:
        return rows[0]["id"], None  # most recent (ordered by updated_at desc)

    ref = ref.strip()
    for r in rows:
        if r["id"] == ref:
            return r["id"], None
    low = ref.lower()
    exact = [r for r in rows if r["name"].strip().lower() == low]
    if len(exact) == 1:
        return exact[0]["id"], None
    if len(exact) > 1:
        return None, f"Multiple docs named '{ref}' — use the doc id."
    subs = [r for r in rows if low in r["name"].lower()]
    if len(subs) == 1:
        return subs[0]["id"], None
    names = ", ".join(r["name"] for r in rows)
    return None, f"No doc matching '{ref}'. You have: {names}."


def _preview(text: str) -> str:
    """Trim long document text for inclusion in an LLM reply."""
    if len(text) <= _MAX_PREVIEW_CHARS:
        return text or "(empty document)"
    return text[:_MAX_PREVIEW_CHARS] + f"\n…(showing {_MAX_PREVIEW_CHARS} of {len(text)} chars)"


class CreateDocSkill(BaseSkill):
    """Create a new blank document."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "create_doc"

    @property
    def description(self) -> str:
        return (
            "Create a new private encrypted document (e.g. 'write me a doc "
            "called Cover Letter'). Returns the doc id to use for later edits. "
            "The user can open and edit it in the Docs tab of the web UI."
        )

    @property
    def category(self) -> str:
        return "docs"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Doc name, e.g. 'Cover Letter'"},
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.docs.store import create_doc

        name = (params.get("name") or "Untitled doc").strip() or "Untitled doc"
        row = await create_doc(self._config, user_id, name)
        return f"Created doc **{row['name']}** (id `{row['id']}`)."


class ListDocsSkill(BaseSkill):
    """List the user's documents."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_docs"

    @property
    def description(self) -> str:
        return "List all of the user's private documents (name + id + last edited)."

    @property
    def category(self) -> str:
        return "docs"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.docs.store import list_docs

        rows = await list_docs(self._config, user_id)
        if not rows:
            return "You have no docs yet."
        lines = [f"- **{r['name']}** (id `{r['id']}`) — updated {r['updated_at']}" for r in rows]
        return f"You have {len(rows)} doc(s):\n" + "\n".join(lines)


class ReadDocSkill(BaseSkill):
    """Read a document's plain text."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "read_doc"

    @property
    def description(self) -> str:
        return (
            "Read a document's contents as plain text so you can reason over or "
            "summarize it. Identify the doc by id or name; omit to read the most "
            "recently edited one."
        )

    @property
    def category(self) -> str:
        return "docs"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Doc id or name (optional — defaults to most recent)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.docs import snapshot as D
        from lazyclaw.docs.store import get_doc

        did, err = await _resolve_doc_id(self._config, user_id, params.get("doc_id"))
        if err:
            return err
        doc = await get_doc(self._config, user_id, did)
        if not doc:
            return "Doc not found."
        text = D.get_text(doc["payload"])
        return f"Doc **{doc['name']}**:\n```\n{_preview(text)}\n```"


class AppendToDocSkill(BaseSkill):
    """Append a paragraph to a document."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "append_to_doc"

    @property
    def description(self) -> str:
        return (
            "Append a paragraph of text to the end of a document (e.g. 'add a "
            "closing paragraph to my cover letter'). Identify the doc by id or "
            "name; omit to use the most recently edited one."
        )

    @property
    def category(self) -> str:
        return "docs"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Doc id or name (optional — defaults to most recent)",
                },
                "text": {"type": "string", "description": "The paragraph text to append"},
            },
            "required": ["text"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.docs import snapshot as D
        from lazyclaw.docs.store import get_doc, save_doc

        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            return "Provide non-empty 'text' to append."
        did, err = await _resolve_doc_id(self._config, user_id, params.get("doc_id"))
        if err:
            return err
        doc = await get_doc(self._config, user_id, did)
        if not doc:
            return "Doc not found."
        updated = D.append_paragraph(doc["payload"], text)
        await save_doc(self._config, user_id, doc["name"], updated, doc_id=did)
        return f"Appended a paragraph to **{doc['name']}**."


class SetDocContentSkill(BaseSkill):
    """Replace a document's entire text."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_doc_content"

    @property
    def description(self) -> str:
        return (
            "Replace a document's entire body with new text (e.g. 'rewrite my "
            "cover letter as follows…'). Newlines start new paragraphs. "
            "Identify the doc by id or name; omit to use the most recent one."
        )

    @property
    def category(self) -> str:
        return "docs"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Doc id or name (optional — defaults to most recent)",
                },
                "text": {"type": "string", "description": "The full new document text"},
            },
            "required": ["text"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.docs import snapshot as D
        from lazyclaw.docs.store import get_doc, save_doc

        text = params.get("text")
        if not isinstance(text, str):
            return "Provide 'text' (the new document body)."
        did, err = await _resolve_doc_id(self._config, user_id, params.get("doc_id"))
        if err:
            return err
        doc = await get_doc(self._config, user_id, did)
        if not doc:
            return "Doc not found."
        updated = D.set_text(doc["payload"], text)
        await save_doc(self._config, user_id, doc["name"], updated, doc_id=did)
        n_paras = len(D.get_paragraphs(updated))
        return f"Replaced the contents of **{doc['name']}** ({n_paras} paragraph(s))."


class SendDocSkill(BaseSkill):
    """Export a doc to a file and deliver it to the user."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "send_doc"

    @property
    def description(self) -> str:
        return (
            "Export a document to a .docx file and send it to the user (e.g. "
            "'send me the cover letter'). Delivers as a Telegram document when "
            "Telegram is configured; always returns the web download link too."
        )

    @property
    def category(self) -> str:
        return "docs"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "Doc id or name (optional)"},
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.docs.docx_io import snapshot_to_docx
        from lazyclaw.docs.store import get_doc
        from lazyclaw.notifications.push import push_telegram_document

        did, err = await _resolve_doc_id(self._config, user_id, params.get("doc_id"))
        if err:
            return err
        doc = await get_doc(self._config, user_id, did)
        if not doc:
            return "Doc not found."

        name = doc["name"]
        content = snapshot_to_docx(doc["payload"])
        filename = f"{name}.docx"
        download_url = f"/api/docs/{did}/export?format=docx"

        sent = await push_telegram_document(
            self._config, content, filename, caption=f"📄 {name}",
        )
        if sent:
            return f"Sent **{filename}** to your Telegram. (Web download: {download_url})"
        return (
            f"Exported **{filename}** ({len(content)} bytes). "
            f"Download it from the Docs tab or at {download_url}."
        )
