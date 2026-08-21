"""Agent skills for PDF page surgery + table extraction.

``lazyclaw/pdf/ops.py`` has carried these since the Documents Workspace shipped,
but no skill ever wrapped them — so ``rotate`` was reachable only from the ✨
in-editor plan, and ``delete_pages`` / ``extract_tables`` / ``flatten`` were
reachable from nowhere at all. The capability existed; the agent just could not
call it.

PDFs are immutable here: every op saves a NEW file and reports its id, matching
the rest of ``skills/builtin/pdf.py``.

``redact_text`` stays deliberately unwrapped. ``ops.redact_text`` draws an opaque
box over the match — it does NOT scrub the content stream, so the text is still
extractable from the file. Handing the agent a tool called "redact" that doesn't
redact is worse than not having one; true redaction is tracked in TODO.md.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.builtin.pdf import _PdfSkill, _resolve_pdf_id

logger = logging.getLogger(__name__)

#: Enough rows to be useful in a chat reply without flooding the context.
_MAX_TABLE_ROWS = 60
_MAX_TABLE_COLS = 12


async def _load(config, user_id: str, params: dict):
    """Resolve + fetch the target PDF. ``(pdf_id, row, None)`` or errors."""
    pid, err = await _resolve_pdf_id(config, user_id, params.get("pdf_id"))
    if err:
        return None, None, err
    from lazyclaw.pdf.store import get_pdf

    row = await get_pdf(config, user_id, pid)
    if not row:
        return None, None, "PDF not found."
    return pid, row, None


def _derived_name(name: str, suffix: str) -> str:
    base = name.rsplit(".pdf", 1)[0] or name
    return f"{base} ({suffix}).pdf"


class RotatePdfSkill(_PdfSkill):
    """Rotate some or all pages of a PDF."""

    @property
    def name(self) -> str:
        return "rotate_pdf"

    @property
    def description(self) -> str:
        return (
            "Rotate pages of a PDF by 90, 180 or 270 degrees clockwise — use for "
            "a scan that came in sideways or upside down. Pass 'pages' as a list "
            "of 1-based page numbers, or omit it to rotate every page. Saves a "
            "new PDF and returns its id."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pdf_id": {"type": "string", "description": "PDF id or name (optional — most recent)"},
                "degrees": {
                    "type": "integer",
                    "enum": [90, 180, 270],
                    "description": "Clockwise rotation",
                },
                "pages": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "1-based page numbers; omit for all pages",
                },
            },
            "required": ["degrees"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pdf import ops
        from lazyclaw.pdf.store import save_pdf

        raw_degrees = params.get("degrees")
        try:
            degrees = int(raw_degrees)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "Pass 'degrees' as 90, 180 or 270."
        if degrees not in (90, 180, 270):
            return "Rotation must be 90, 180 or 270 degrees."

        pid, pdf, err = await _load(self._config, user_id, params)
        if err:
            return err
        assert pdf is not None

        pages = _page_list(params.get("pages"))
        if pages is False:
            return "Each page must be a 1-based integer page number."
        try:
            data = ops.rotate(pdf["bytes"], degrees, pages)
        except ops.PdfError as exc:
            return f"Could not rotate: {exc}"

        row = await save_pdf(
            self._config, user_id, _derived_name(pdf["name"], f"rotated {degrees}°"), data
        )
        scope = "all pages" if pages is None else f"page(s) {', '.join(map(str, pages))}"
        return (
            f"Rotated {scope} by {degrees}° → **{row['name']}** (id `{row['id']}`)."
        )


class DeletePdfPagesSkill(_PdfSkill):
    """Drop pages from a PDF."""

    @property
    def name(self) -> str:
        return "delete_pdf_pages"

    @property
    def description(self) -> str:
        return (
            "Remove pages from a PDF — use for 'drop the last page', 'delete the "
            "blank pages', 'remove page 3'. Pass 'pages' as a list of 1-based "
            "page numbers. Saves a new PDF (the original is untouched) and "
            "returns its id."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pdf_id": {"type": "string", "description": "PDF id or name (optional — most recent)"},
                "pages": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "1-based page numbers to remove",
                },
            },
            "required": ["pages"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pdf import ops
        from lazyclaw.pdf.store import save_pdf

        pages = _page_list(params.get("pages"))
        if pages is False or not pages:
            return "Provide 'pages' as a non-empty list of 1-based page numbers."

        pid, pdf, err = await _load(self._config, user_id, params)
        if err:
            return err
        assert pdf is not None

        try:
            data = ops.delete_pages(pdf["bytes"], pages)
        except ops.PdfError as exc:
            return f"Could not delete those pages: {exc}"

        row = await save_pdf(
            self._config, user_id, _derived_name(pdf["name"], "pages removed"), data
        )
        return (
            f"Removed page(s) {', '.join(map(str, pages))} → "
            f"**{row['name']}** (id `{row['id']}`), {row.get('pages', '?')} page(s) left."
        )


class FlattenPdfSkill(_PdfSkill):
    """Flatten form fields into the page content."""

    @property
    def name(self) -> str:
        return "flatten_pdf"

    @property
    def description(self) -> str:
        return (
            "Flatten a PDF's interactive form fields into the page itself, so the "
            "filled values can no longer be edited or cleared. Use after "
            "fill_pdf_form when sending a completed form to someone. Saves a new "
            "PDF and returns its id."
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
        from lazyclaw.pdf import ops
        from lazyclaw.pdf.store import save_pdf

        pid, pdf, err = await _load(self._config, user_id, params)
        if err:
            return err
        assert pdf is not None

        try:
            data = ops.flatten(pdf["bytes"])
        except ops.PdfError as exc:
            return f"Could not flatten: {exc}"

        row = await save_pdf(
            self._config, user_id, _derived_name(pdf["name"], "flattened"), data
        )
        return f"Flattened → **{row['name']}** (id `{row['id']}`)."


class ExtractPdfTablesSkill(_PdfSkill):
    """Pull tabular data out of a PDF."""

    @property
    def name(self) -> str:
        return "extract_pdf_tables"

    @property
    def description(self) -> str:
        return (
            "Extract TABLES from a PDF as rows and columns — use when the user "
            "wants the numbers out of an invoice, statement or report rather than "
            "its prose. Returns the grid; pair it with create_sheet + set_cells "
            "to turn a PDF table into a real spreadsheet. For plain prose use "
            "read_pdf instead."
        )

    @property
    def read_only(self) -> bool:
        return True

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
        from lazyclaw.pdf import ops

        pid, pdf, err = await _load(self._config, user_id, params)
        if err:
            return err
        assert pdf is not None

        try:
            # ops.extract_tables scans the whole document — there is no page
            # filter, so the schema deliberately doesn't offer one.
            tables = ops.extract_tables(pdf["bytes"])
        except ops.PdfError as exc:
            return f"Could not extract tables: {exc}"
        if not tables:
            return (
                f"No tables found in **{pdf['name']}**. It may be a scan (no text "
                f"layer) or laid out without ruling lines — try read_pdf."
            )

        chunks = [f"Found {len(tables)} table(s) in **{pdf['name']}**:"]
        for i, table in enumerate(tables, start=1):
            rows = table[:_MAX_TABLE_ROWS]
            lines = [
                " | ".join(
                    "" if cell is None else str(cell)
                    for cell in row[:_MAX_TABLE_COLS]
                )
                for row in rows
            ]
            note = ""
            if len(table) > len(rows):
                note = f"\n…({len(rows)} of {len(table)} rows)"
            chunks.append(f"\n**Table {i}**\n```\n" + "\n".join(lines) + f"\n```{note}")
        return "\n".join(chunks)


def _page_list(raw) -> list[int] | None | bool:
    """``[1, 2]`` → page numbers, ``None`` → "all", ``False`` → invalid.

    The three-way return keeps "omitted" (a legitimate 'every page') distinct
    from "given but malformed", which has to be reported rather than silently
    treated as all pages — rotating an entire document when the user asked for
    one page is a destructive surprise.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return False
    out: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return False
        try:
            page = int(value)
        except (TypeError, ValueError):
            return False
        if page < 1:
            return False
        out.append(page)
    return out
