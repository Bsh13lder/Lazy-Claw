"""Agent skills for private encrypted PDF files.

NL control over :mod:`lazyclaw.pdf.store` + :mod:`lazyclaw.pdf.ops`. The agent
lists/reads PDFs, merges/splits, fills AcroForm fields, stamps text or a
signature, generates new PDFs, and delivers a PDF to the user. Channel-agnostic:
identical behaviour in Telegram, Web UI chat, and CLI.

PDFs are referenced by id OR name (exact, then substring) via
:func:`_resolve_pdf_id`. Operations that produce a NEW PDF save it as a new
``pdf_files`` entry and report its id — the source is left untouched (immutable
edits, matching the project's no-mutation rule).

"Editing" here = fill / stamp / merge / split / rotate / delete pages / extract
/ generate. It does NOT mean reflow-editing existing body text (not possible).
"""

from __future__ import annotations

import logging
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_CATEGORY = "pdf"
_MAX_TEXT_PREVIEW = 4000


async def _resolve_pdf_id(
    config: Any, user_id: str, ref: str | None
) -> tuple[str | None, str | None]:
    """Resolve a PDF reference (id, exact name, or substring) to an id.

    Returns ``(pdf_id, None)`` on success or ``(None, error_message)``.
    With no ref, picks the most recently updated PDF.
    """
    from lazyclaw.pdf.store import list_pdfs

    rows = await list_pdfs(config, user_id)
    if not rows:
        return None, "You have no PDFs yet — upload or generate one first."
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
        # List candidate ids so the caller can retry with a concrete id instead
        # of repeating the ambiguous name-based call forever (stuck loop).
        opts = "; ".join(
            f"id={r['id']}"
            + (f" (updated {r['updated_at']})" if r.get("updated_at") else "")
            for r in exact
        )
        return None, (
            f"Multiple PDFs named '{ref}'. Retry with ONE of these ids as the "
            f"PDF reference — {opts}"
        )
    subs = [r for r in rows if low in r["name"].lower()]
    if len(subs) == 1:
        return subs[0]["id"], None
    names = ", ".join(r["name"] for r in rows)
    return None, f"No PDF matching '{ref}'. You have: {names}."


class _PdfSkill(BaseSkill):
    """Shared base: config injection + the ``pdf`` category."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def category(self) -> str:
        return _CATEGORY


class ListPdfsSkill(_PdfSkill):
    """List the user's PDF files."""

    @property
    def name(self) -> str:
        return "list_pdfs"

    @property
    def description(self) -> str:
        return "List all of the user's private PDF files (name + id + page count + last edited)."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pdf.store import list_pdfs

        rows = await list_pdfs(self._config, user_id)
        if not rows:
            return "You have no PDFs yet."
        lines = [
            f"- **{r['name']}** (id `{r['id']}`) — "
            f"{r['pages'] if r['pages'] is not None else '?'} page(s), "
            f"updated {r['updated_at']}"
            for r in rows
        ]
        return f"You have {len(rows)} PDF(s):\n" + "\n".join(lines)


class ReadPdfSkill(_PdfSkill):
    """Read a PDF's extracted text."""

    @property
    def name(self) -> str:
        return "read_pdf"

    @property
    def description(self) -> str:
        return (
            "Read a PDF's text content so you can reason over it (e.g. 'what "
            "does the invoice say'). Identify the PDF by id or name; omit to "
            "read the most recently edited one."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pdf_id": {
                    "type": "string",
                    "description": "PDF id or name (optional — defaults to most recent)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pdf import ops
        from lazyclaw.pdf.store import get_pdf

        pid, err = await _resolve_pdf_id(self._config, user_id, params.get("pdf_id"))
        if err:
            return err
        pdf = await get_pdf(self._config, user_id, pid)
        if not pdf:
            return "PDF not found."
        try:
            text = ops.extract_text(pdf["bytes"])
        except ops.PdfError as exc:
            return f"Could not read PDF: {exc}"
        text = text.strip() or "(no extractable text — the PDF may be scanned/image-only)"
        truncated = ""
        if len(text) > _MAX_TEXT_PREVIEW:
            text = text[:_MAX_TEXT_PREVIEW]
            truncated = "\n…(truncated)"
        return f"**{pdf['name']}** ({pdf['pages']} page(s)):\n```\n{text}{truncated}\n```"


class MergePdfsSkill(_PdfSkill):
    """Merge several PDFs into one new PDF."""

    @property
    def name(self) -> str:
        return "merge_pdfs"

    @property
    def description(self) -> str:
        return (
            "Combine two or more PDFs into a single new PDF, in the order given. "
            "Pass a list of PDF ids or names. Saves the result as a new PDF and "
            "returns its id."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pdf_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "PDF ids or names to merge, in order (>= 2)",
                },
                "name": {
                    "type": "string",
                    "description": "Name for the merged PDF (optional)",
                },
            },
            "required": ["pdf_ids"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pdf import ops
        from lazyclaw.pdf.store import get_pdf, save_pdf

        refs = params.get("pdf_ids")
        if not isinstance(refs, list) or len(refs) < 2:
            return "Provide a list of at least two PDF ids or names to merge."
        parts: list[bytes] = []
        for ref in refs:
            pid, err = await _resolve_pdf_id(self._config, user_id, ref)
            if err:
                return err
            pdf = await get_pdf(self._config, user_id, pid)
            if not pdf:
                return f"PDF '{ref}' not found."
            parts.append(pdf["bytes"])
        try:
            merged = ops.merge(parts)
        except ops.PdfError as exc:
            return f"Could not merge: {exc}"
        name = (params.get("name") or "merged.pdf").strip() or "merged.pdf"
        row = await save_pdf(self._config, user_id, name, merged)
        return f"Merged {len(parts)} PDFs into **{row['name']}** (id `{row['id']}`, {row['pages']} pages)."


class SplitPdfSkill(_PdfSkill):
    """Split a PDF into separate PDFs."""

    @property
    def name(self) -> str:
        return "split_pdf"

    @property
    def description(self) -> str:
        return (
            "Split a PDF into separate PDFs. Pass 'ranges' as a list of "
            "[start, end] 1-based inclusive page ranges (e.g. [[1,3],[4,6]]); "
            "omit ranges to split into one PDF per page. Saves each part as a "
            "new PDF and returns their ids."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pdf_id": {"type": "string", "description": "PDF id or name (optional — most recent)"},
                "ranges": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "description": "1-based inclusive [start,end] ranges; omit for one PDF per page",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pdf import ops
        from lazyclaw.pdf.store import get_pdf, save_pdf

        pid, err = await _resolve_pdf_id(self._config, user_id, params.get("pdf_id"))
        if err:
            return err
        pdf = await get_pdf(self._config, user_id, pid)
        if not pdf:
            return "PDF not found."

        raw_ranges = params.get("ranges")
        ranges: list[tuple[int, int]] | None = None
        if raw_ranges:
            try:
                ranges = [(int(r[0]), int(r[1])) for r in raw_ranges]
            except (TypeError, IndexError, ValueError):
                return "Each range must be a [start, end] pair of integers."
        try:
            parts = ops.split(pdf["bytes"], ranges)
        except ops.PdfError as exc:
            return f"Could not split: {exc}"

        base = pdf["name"].rsplit(".pdf", 1)[0] or pdf["name"]
        saved: list[str] = []
        for i, part in enumerate(parts, start=1):
            row = await save_pdf(self._config, user_id, f"{base} - part {i}.pdf", part)
            saved.append(f"**{row['name']}** (id `{row['id']}`)")
        return f"Split into {len(saved)} PDF(s):\n" + "\n".join(f"- {s}" for s in saved)


class FillPdfFormSkill(_PdfSkill):
    """Fill an AcroForm PDF's fields."""

    @property
    def name(self) -> str:
        return "fill_pdf_form"

    @property
    def description(self) -> str:
        return (
            "Fill the interactive form fields of a PDF (AcroForm). Pass 'values' "
            "as a {field_name: value} mapping. Saves a new filled PDF and returns "
            "its id. If you don't know the field names, the result of a failed "
            "fill lists them."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pdf_id": {"type": "string", "description": "PDF id or name (optional — most recent)"},
                "values": {
                    "type": "object",
                    "description": "Mapping of form field name → value",
                    "additionalProperties": {"type": ["string", "number", "boolean"]},
                },
                "flatten": {
                    "type": "boolean",
                    "description": "Bake the values in so the form is no longer editable (default false)",
                },
            },
            "required": ["values"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pdf import ops
        from lazyclaw.pdf.store import get_pdf, save_pdf

        values = params.get("values")
        if not isinstance(values, dict) or not values:
            return "Provide a non-empty 'values' mapping of field name → value."
        pid, err = await _resolve_pdf_id(self._config, user_id, params.get("pdf_id"))
        if err:
            return err
        pdf = await get_pdf(self._config, user_id, pid)
        if not pdf:
            return "PDF not found."

        fields = ops.get_form_fields(pdf["bytes"])
        if not fields:
            return "This PDF has no fillable form fields."
        try:
            filled = ops.fill_form(pdf["bytes"], values)
            if params.get("flatten"):
                filled = ops.flatten(filled)
        except ops.PdfError as exc:
            names = ", ".join(sorted(fields)) or "(none)"
            return f"Could not fill form: {exc}. Available fields: {names}."

        base = pdf["name"].rsplit(".pdf", 1)[0] or pdf["name"]
        row = await save_pdf(self._config, user_id, f"{base} - filled.pdf", filled)
        return (
            f"Filled {len(values)} field(s) → **{row['name']}** (id `{row['id']}`)."
        )


class AddTextToPdfSkill(_PdfSkill):
    """Stamp text or a typed signature onto a PDF."""

    @property
    def name(self) -> str:
        return "add_text_to_pdf"

    @property
    def description(self) -> str:
        return (
            "Stamp text or a typed signature onto a PDF at exact positions "
            "(e.g. add a name, date, or signature line). Pass 'items', each "
            "{page, x, y, text, size?, font?}. Coordinates are PDF points from "
            "the bottom-left of the page (72 pt = 1 inch). Saves a new PDF and "
            "returns its id."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pdf_id": {"type": "string", "description": "PDF id or name (optional — most recent)"},
                "items": {
                    "type": "array",
                    "description": "Text stamps to apply",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page": {"type": "integer", "description": "1-based page number"},
                            "x": {"type": "number", "description": "X in points from bottom-left"},
                            "y": {"type": "number", "description": "Y in points from bottom-left"},
                            "text": {"type": "string", "description": "Text to draw"},
                            "size": {"type": "number", "description": "Font size in points (default 12)"},
                            "font": {"type": "string", "description": "Font name (default Helvetica)"},
                        },
                        "required": ["page", "x", "y", "text"],
                    },
                },
            },
            "required": ["items"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pdf import ops
        from lazyclaw.pdf.store import get_pdf, save_pdf

        items = params.get("items")
        if not isinstance(items, list) or not items:
            return "Provide a non-empty 'items' list of {page, x, y, text}."
        pid, err = await _resolve_pdf_id(self._config, user_id, params.get("pdf_id"))
        if err:
            return err
        pdf = await get_pdf(self._config, user_id, pid)
        if not pdf:
            return "PDF not found."
        try:
            stamped = ops.overlay_text(pdf["bytes"], items)
        except ops.PdfError as exc:
            return f"Could not stamp text: {exc}"

        base = pdf["name"].rsplit(".pdf", 1)[0] or pdf["name"]
        row = await save_pdf(self._config, user_id, f"{base} - signed.pdf", stamped)
        return f"Stamped {len(items)} item(s) → **{row['name']}** (id `{row['id']}`)."


class GeneratePdfSkill(_PdfSkill):
    """Generate a new PDF from plain text."""

    @property
    def name(self) -> str:
        return "generate_pdf"

    @property
    def description(self) -> str:
        return (
            "Create a brand-new PDF from text (e.g. 'make me a PDF of this "
            "letter'). Blank lines separate paragraphs; an optional title is "
            "rendered as a heading. Saves it to the user's PDF store and returns "
            "its id."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Body text (blank lines = paragraph breaks)"},
                "title": {"type": "string", "description": "Optional heading"},
                "name": {"type": "string", "description": "File name (optional)"},
            },
            "required": ["text"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pdf import ops
        from lazyclaw.pdf.store import save_pdf

        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            return "Provide some 'text' to put in the PDF."
        title = params.get("title")
        try:
            data = ops.generate_from_text(text, title=title)
        except ops.PdfError as exc:
            return f"Could not generate PDF: {exc}"
        name = (params.get("name") or (title or "document")).strip() or "document"
        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"
        row = await save_pdf(self._config, user_id, name, data)
        return f"Generated **{row['name']}** (id `{row['id']}`, {row['pages']} page(s))."


class SendPdfSkill(_PdfSkill):
    """Deliver a PDF to the user."""

    @property
    def name(self) -> str:
        return "send_pdf"

    @property
    def description(self) -> str:
        return (
            "Send a PDF to the user (e.g. 'send me the contract'). Delivers as a "
            "Telegram document when Telegram is configured; always returns the "
            "web download link too."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pdf_id": {"type": "string", "description": "PDF id or name (optional — most recent)"},
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.notifications.push import push_telegram_document
        from lazyclaw.pdf.store import get_pdf

        pid, err = await _resolve_pdf_id(self._config, user_id, params.get("pdf_id"))
        if err:
            return err
        pdf = await get_pdf(self._config, user_id, pid)
        if not pdf:
            return "PDF not found."

        name = pdf["name"]
        if not name.lower().endswith(".pdf"):
            filename = f"{name}.pdf"
        else:
            filename = name
        content = pdf["bytes"]
        download_url = f"/api/pdf/{pid}/download"

        sent = await push_telegram_document(
            self._config, content, filename, caption=f"📄 {name}",
        )
        if sent:
            return f"Sent **{filename}** to your Telegram. (Web download: {download_url})"
        return (
            f"Prepared **{filename}** ({len(content)} bytes). "
            f"Download it from the PDF tab or at {download_url}."
        )
