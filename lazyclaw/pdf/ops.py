"""Pure functions on PDF bytes (input bytes → output bytes/str).

Every function is stateless: it takes raw PDF ``bytes`` (and parameters) and
returns either raw ``bytes`` (a new PDF) or extracted data. No I/O, no encryption,
no database — the store and skills layer those on top.

Engines (permissive licences only):
- pypdf (BSD): structural ops — page count, merge/split/rotate/delete, AcroForm
  fields + fill, flatten, page-overlay merge.
- pdfplumber (MIT): text + table extraction.
- reportlab (BSD): overlay generation (text/signature stamping, redaction
  rectangles) and from-scratch PDF generation.

NEVER import PyMuPDF/fitz or borb (AGPL) here.

All operations defend against malformed input by raising :class:`PdfError`
(a ``ValueError`` subclass) with a user-facing message, rather than leaking a
raw library traceback. Output PDFs always begin with the ``%PDF`` magic.
"""

from __future__ import annotations

import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PdfError(ValueError):
    """Raised when a PDF operation cannot be completed (bad input, etc.)."""


_PDF_MAGIC = b"%PDF"


def _validate_pdf_bytes(data: bytes) -> None:
    """Fail fast on obviously non-PDF input."""
    if not isinstance(data, (bytes, bytearray)):
        raise PdfError("PDF data must be bytes.")
    if not data:
        raise PdfError("PDF data is empty.")
    # The %PDF header may be preceded by a few junk bytes in the wild; accept
    # it anywhere in the first 1 KB rather than only at offset 0.
    if _PDF_MAGIC not in bytes(data[:1024]):
        raise PdfError("Data does not look like a PDF (missing %PDF header).")


def is_pdf(data: bytes) -> bool:
    """True if *data* carries the ``%PDF`` magic within its first 1 KB."""
    try:
        _validate_pdf_bytes(data)
        return True
    except PdfError:
        return False


# ---------------------------------------------------------------------------
# pypdf helpers
# ---------------------------------------------------------------------------

def _tolerant_reader_cls():
    """A ``PdfReader`` subclass that survives indirect-to-indirect object bodies.

    pypdf 6.x made :pyattr:`IndirectObject.indirect_reference` a *read-only*
    property. Some real-world PDFs (certain scanners / form generators) emit an
    object whose body is a *bare* indirect reference, e.g.::

        4 0 obj
        5 0 R
        endobj

    When pypdf resolves such an object it parses it as an ``IndirectObject`` and
    then, in ``cache_indirect_object``, attempts ``obj.indirect_reference = ...``
    on it — which now raises ``AttributeError: property 'indirect_reference' of
    'IndirectObject' object has no setter`` and aborts every read/clone/stamp.

    The fix collapses the chain at cache time: when the object being cached *is*
    an ``IndirectObject``, resolve it to its concrete target (one hop, with
    self-reference and unresolvable targets degrading to ``NullObject``) before
    delegating to the parent — so a real ``PdfObject`` is cached and the
    read-only property is never written. Built lazily so the import cost (and
    pypdf's exact class layout) is paid only when a PDF op actually runs.
    """
    from pypdf import PdfReader
    from pypdf.generic import IndirectObject, NullObject

    class _TolerantPdfReader(PdfReader):
        def cache_indirect_object(self, generation, idnum, obj):  # type: ignore[override]
            if isinstance(obj, IndirectObject):
                if obj.idnum == idnum and obj.generation == generation:
                    # Self-reference (e.g. "4 0 obj  4 0 R"): cannot resolve —
                    # substitute a null rather than recurse forever.
                    obj = NullObject()
                else:
                    try:
                        resolved = obj.get_object()
                    except Exception:  # noqa: BLE001 — missing / circular target
                        resolved = None
                    # A concrete object collapses the chain; anything else
                    # (missing target, deeper indirection) becomes null so the
                    # read-only ``indirect_reference`` property is never set.
                    obj = (
                        resolved
                        if resolved is not None and not isinstance(resolved, IndirectObject)
                        else NullObject()
                    )
            return super().cache_indirect_object(generation, idnum, obj)

    return _TolerantPdfReader


def _reader(data: bytes):
    """Build a pypdf reader, surfacing parse errors as :class:`PdfError`.

    Uses a tolerant reader (see :func:`_tolerant_reader_cls`) so PDFs carrying
    bare indirect-to-indirect object bodies load instead of crashing on pypdf
    6.x's read-only ``indirect_reference`` property.
    """
    from pypdf.errors import PdfReadError

    _validate_pdf_bytes(data)
    reader_cls = _tolerant_reader_cls()
    try:
        return reader_cls(io.BytesIO(bytes(data)))
    except PdfReadError as exc:
        raise PdfError(f"Could not read PDF: {exc}") from exc
    except Exception as exc:  # malformed xref, encryption, etc.
        raise PdfError(f"Could not read PDF: {exc}") from exc


def _writer_to_bytes(writer) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    out = buf.getvalue()
    if not out.startswith(_PDF_MAGIC):
        raise PdfError("Internal error: produced bytes are not a valid PDF.")
    return out


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

def page_count(data: bytes) -> int:
    """Number of pages in the PDF."""
    reader = _reader(data)
    return len(reader.pages)


def extract_text(data: bytes) -> str:
    """Extract all text, joining pages with a form-feed + newline.

    Uses pdfplumber (MIT). Pages with no extractable text yield an empty
    string for that page. Best-effort: per-page failures are logged and
    skipped rather than aborting the whole extraction.
    """
    import pdfplumber

    _validate_pdf_bytes(data)
    parts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(bytes(data))) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    parts.append(page.extract_text() or "")
                except Exception as exc:  # noqa: BLE001 — per-page resilience
                    logger.warning("extract_text failed on page %d: %s", i, exc)
                    parts.append("")
    except Exception as exc:
        raise PdfError(f"Could not extract text: {exc}") from exc
    # Form-feed separates pages; newline keeps it readable.
    return "\f\n".join(parts)


def extract_tables(data: bytes) -> list[list[list[str]]]:
    """Extract tables as a list of tables; each table is rows of string cells.

    Best-effort via pdfplumber's table detector. Returns an empty list when no
    tables are found. ``None`` cells are coerced to empty strings.
    """
    import pdfplumber

    _validate_pdf_bytes(data)
    tables: list[list[list[str]]] = []
    try:
        with pdfplumber.open(io.BytesIO(bytes(data))) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    for raw in page.extract_tables() or []:
                        tables.append(
                            [["" if c is None else str(c) for c in row] for row in raw]
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("extract_tables failed on page %d: %s", i, exc)
    except Exception as exc:
        raise PdfError(f"Could not extract tables: {exc}") from exc
    return tables


# ---------------------------------------------------------------------------
# Structural transforms
# ---------------------------------------------------------------------------

def merge(parts: list[bytes]) -> bytes:
    """Concatenate several PDFs (in order) into one."""
    from pypdf import PdfWriter

    if not parts:
        raise PdfError("merge() needs at least one PDF.")
    writer = PdfWriter()
    for idx, part in enumerate(parts):
        reader = _reader(part)
        for page in reader.pages:
            writer.add_page(page)
        logger.debug("merge: appended part %d (%d pages)", idx, len(reader.pages))
    return _writer_to_bytes(writer)


def _normalize_ranges(
    ranges: list[tuple[int, int]] | None, n: int
) -> list[tuple[int, int]]:
    """Validate / default page ranges (1-based, inclusive) against page count.

    ``None`` → one single-page range per page. Out-of-bounds or inverted
    ranges raise :class:`PdfError`.
    """
    if n <= 0:
        raise PdfError("PDF has no pages.")
    if ranges is None:
        return [(p, p) for p in range(1, n + 1)]
    out: list[tuple[int, int]] = []
    for r in ranges:
        try:
            start, end = int(r[0]), int(r[1])
        except (TypeError, IndexError, ValueError) as exc:
            raise PdfError(f"Invalid range {r!r}; expected (start, end).") from exc
        if start < 1 or end > n or start > end:
            raise PdfError(
                f"Range {start}-{end} out of bounds for a {n}-page PDF."
            )
        out.append((start, end))
    return out


def split(
    data: bytes, ranges: list[tuple[int, int]] | None = None
) -> list[bytes]:
    """Split into one PDF per range (1-based, inclusive).

    ``ranges=None`` produces one PDF per page. Each output is a standalone PDF.
    """
    from pypdf import PdfWriter

    reader = _reader(data)
    n = len(reader.pages)
    normalized = _normalize_ranges(ranges, n)

    outputs: list[bytes] = []
    for start, end in normalized:
        writer = PdfWriter()
        for p in range(start - 1, end):  # 1-based inclusive → 0-based slice
            writer.add_page(reader.pages[p])
        outputs.append(_writer_to_bytes(writer))
    return outputs


def _resolve_page_set(pages: list[int] | None, n: int) -> set[int]:
    """1-based page list → validated set of 0-based indices. ``None`` → all."""
    if pages is None:
        return set(range(n))
    resolved: set[int] = set()
    for p in pages:
        try:
            pi = int(p)
        except (TypeError, ValueError) as exc:
            raise PdfError(f"Invalid page number {p!r}.") from exc
        if pi < 1 or pi > n:
            raise PdfError(f"Page {pi} out of bounds for a {n}-page PDF.")
        resolved.add(pi - 1)
    return resolved


def rotate(data: bytes, degrees: int, pages: list[int] | None = None) -> bytes:
    """Rotate pages clockwise by *degrees* (must be a multiple of 90).

    ``pages`` is a 1-based list; ``None`` rotates every page.
    """
    from pypdf import PdfWriter

    if degrees % 90 != 0:
        raise PdfError("Rotation must be a multiple of 90 degrees.")
    reader = _reader(data)
    n = len(reader.pages)
    targets = _resolve_page_set(pages, n)

    writer = PdfWriter()
    for idx, page in enumerate(reader.pages):
        if idx in targets:
            page.rotate(degrees)
        writer.add_page(page)
    return _writer_to_bytes(writer)


def delete_pages(data: bytes, pages: list[int]) -> bytes:
    """Return a new PDF with the given 1-based *pages* removed."""
    from pypdf import PdfWriter

    if not pages:
        raise PdfError("delete_pages() needs at least one page number.")
    reader = _reader(data)
    n = len(reader.pages)
    to_drop = _resolve_page_set(pages, n)
    if len(to_drop) >= n:
        raise PdfError("Refusing to delete every page.")

    writer = PdfWriter()
    for idx, page in enumerate(reader.pages):
        if idx not in to_drop:
            writer.add_page(page)
    return _writer_to_bytes(writer)


# ---------------------------------------------------------------------------
# AcroForm forms
# ---------------------------------------------------------------------------

def get_form_fields(data: bytes) -> dict[str, Any]:
    """Map of AcroForm field name → current value (best-effort).

    Returns an empty dict when the PDF has no interactive form.
    """
    reader = _reader(data)
    try:
        fields = reader.get_fields()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_form_fields failed: %s", exc)
        return {}
    if not fields:
        return {}
    out: dict[str, Any] = {}
    for name, field in fields.items():
        try:
            value = field.get("/V")
        except Exception:  # noqa: BLE001
            value = None
        out[str(name)] = "" if value is None else str(value)
    return out


def fill_form(data: bytes, values: dict[str, Any]) -> bytes:
    """Fill AcroForm text/checkbox fields with *values* (name → value).

    Unknown field names are ignored. The output keeps fields interactive;
    call :func:`flatten` afterwards to bake them in.
    """
    from pypdf import PdfWriter

    if not isinstance(values, dict) or not values:
        raise PdfError("fill_form() needs a non-empty {field: value} mapping.")
    reader = _reader(data)
    if not get_form_fields(data):
        raise PdfError("This PDF has no fillable form fields.")

    writer = PdfWriter()
    writer.append(reader)
    str_values = {str(k): str(v) for k, v in values.items()}
    try:
        # NeedAppearances=True so viewers re-render the new values.
        try:
            writer.set_need_appearances_writer(True)
        except Exception:  # noqa: BLE001 — older/newer pypdf signature drift
            pass
        for page in writer.pages:
            writer.update_page_form_field_values(page, str_values, auto_regenerate=False)
    except Exception as exc:
        raise PdfError(f"Could not fill form: {exc}") from exc
    return _writer_to_bytes(writer)


# ---------------------------------------------------------------------------
# Overlays (text / signature stamping, redaction)
# ---------------------------------------------------------------------------

def _page_size(reader, page_index: int) -> tuple[float, float]:
    box = reader.pages[page_index].mediabox
    return float(box.width), float(box.height)


def _build_overlay(
    per_page_draw: dict[int, Any], page_sizes: dict[int, tuple[float, float]]
) -> bytes:
    """Render a transparent overlay PDF; ``per_page_draw[i](canvas)`` draws page i.

    Pages not in ``per_page_draw`` are emitted blank so page indices line up
    with the base document during the merge.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    max_idx = max(page_sizes) if page_sizes else -1
    c = canvas.Canvas(buf, pagesize=letter)
    for idx in range(max_idx + 1):
        w, h = page_sizes.get(idx, letter)
        c.setPageSize((w, h))
        draw = per_page_draw.get(idx)
        if draw is not None:
            draw(c)
        c.showPage()
    c.save()
    return buf.getvalue()


def _merge_overlay(base: bytes, overlay: bytes, touched: set[int]) -> bytes:
    """Stamp the overlay PDF on top of the base, page by page, for *touched*.

    Pages are cloned into the writer *first* (``clone_from``) so ``merge_page``
    operates on writer-attached pages — pypdf 6.x deprecates merging detached
    reader pages as unreliable.

    The base is loaded through :func:`_reader` (tolerant) and the *reader* is
    handed to ``clone_from`` — NOT the raw bytes. ``PdfWriter(clone_from=<bytes>)``
    would build its own stock ``PdfReader`` internally and re-trigger the pypdf
    6.x ``indirect_reference`` setter crash on PDFs with bare indirect-to-indirect
    object bodies.
    """
    from pypdf import PdfWriter

    _validate_pdf_bytes(base)
    overlay_reader = _reader(overlay)
    base_reader = _reader(base)
    try:
        writer = PdfWriter(clone_from=base_reader)
    except Exception as exc:
        raise PdfError(f"Could not load PDF for stamping: {exc}") from exc
    for idx, page in enumerate(writer.pages):
        if idx in touched and idx < len(overlay_reader.pages):
            try:
                page.merge_page(overlay_reader.pages[idx])
            except Exception as exc:  # noqa: BLE001
                raise PdfError(f"Could not stamp overlay on page {idx + 1}: {exc}") from exc
    return _writer_to_bytes(writer)


def overlay_text(data: bytes, items: list[dict[str, Any]]) -> bytes:
    """Stamp text (or a typed signature) onto pages.

    Each *item* is a dict with:
      - ``page``: 1-based page number (required)
      - ``x``, ``y``: position in PDF points from the bottom-left (required)
      - ``text``: the string to draw (required)
      - ``size``: font size in points (default 12)
      - ``font``: reportlab font name (default ``Helvetica``)

    Coordinates are PDF user-space points (72 per inch), origin bottom-left —
    the same system reportlab uses.
    """
    from reportlab.pdfbase.pdfmetrics import getRegisteredFontNames, standardFonts

    if not isinstance(items, list) or not items:
        raise PdfError("overlay_text() needs a non-empty list of items.")
    reader = _reader(data)
    n = len(reader.pages)
    # Base-14 fonts aren't in getRegisteredFontNames() until first used, so
    # union them in explicitly — otherwise the default "Helvetica" is rejected.
    available_fonts = set(getRegisteredFontNames()) | set(standardFonts)

    # Group draw instructions per 0-based page index.
    by_page: dict[int, list[dict[str, Any]]] = {}
    page_sizes: dict[int, tuple[float, float]] = {}
    for item in items:
        try:
            page_1 = int(item["page"])
            x = float(item["x"])
            y = float(item["y"])
            text = str(item["text"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PdfError(
                "Each overlay item needs page, x, y, text "
                f"(got {item!r}): {exc}"
            ) from exc
        if page_1 < 1 or page_1 > n:
            raise PdfError(f"Page {page_1} out of bounds for a {n}-page PDF.")
        idx = page_1 - 1
        size = float(item.get("size", 12) or 12)
        font = str(item.get("font") or "Helvetica")
        if font not in available_fonts:
            font = "Helvetica"
        by_page.setdefault(idx, []).append(
            {"x": x, "y": y, "text": text, "size": size, "font": font}
        )
        page_sizes[idx] = _page_size(reader, idx)

    def make_draw(draws: list[dict[str, Any]]):
        def _draw(c) -> None:
            for d in draws:
                c.setFont(d["font"], d["size"])
                c.drawString(d["x"], d["y"], d["text"])
        return _draw

    per_page_draw = {idx: make_draw(draws) for idx, draws in by_page.items()}
    overlay = _build_overlay(per_page_draw, page_sizes)
    return _merge_overlay(data, overlay, set(by_page))


def redact_text(data: bytes, page: int, rects: list[tuple]) -> bytes:
    """Draw filled black rectangles over regions of a page.

    *page* is 1-based; *rects* is a list of ``(x0, y0, x1, y1)`` boxes in PDF
    points (origin bottom-left).

    LIMITATION (kept honest): this performs a *visual* mask only — it paints
    opaque rectangles on top of the existing content. The text and image data
    underneath remain in the file's content stream and can still be recovered
    by copy/paste or a parser. True redaction (scrubbing the underlying content
    stream so the data is gone) is a future enhancement; do NOT rely on this to
    remove sensitive data from a document you will hand to an adversary.
    """
    from reportlab.lib.colors import black

    if not isinstance(rects, list) or not rects:
        raise PdfError("redact_text() needs at least one rectangle.")
    reader = _reader(data)
    n = len(reader.pages)
    if page < 1 or page > n:
        raise PdfError(f"Page {page} out of bounds for a {n}-page PDF.")
    idx = page - 1

    boxes: list[tuple[float, float, float, float]] = []
    for r in rects:
        try:
            x0, y0, x1, y1 = (float(r[0]), float(r[1]), float(r[2]), float(r[3]))
        except (TypeError, IndexError, ValueError) as exc:
            raise PdfError(f"Invalid rect {r!r}; expected (x0, y0, x1, y1).") from exc
        boxes.append((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))

    def _draw(c) -> None:
        c.setFillColor(black)
        for x, y, w, h in boxes:
            c.rect(x, y, w, h, stroke=0, fill=1)

    overlay = _build_overlay({idx: _draw}, {idx: _page_size(reader, idx)})
    return _merge_overlay(data, overlay, {idx})


# ---------------------------------------------------------------------------
# Flatten + generate
# ---------------------------------------------------------------------------

def flatten(data: bytes) -> bytes:
    """Flatten interactive form fields so their values become static content.

    Uses pypdf's ``update_page_form_field_values(..., flatten=True)`` which
    bakes the current field values into the page content and drops the
    interactive widgets. PDFs without a form are returned re-serialized
    (a no-op flatten is harmless).
    """
    from pypdf import PdfWriter

    reader = _reader(data)
    writer = PdfWriter()
    writer.append(reader)
    fields = get_form_fields(data)
    if fields:
        try:
            writer.update_page_form_field_values(
                None,  # all pages
                {k: ("" if v is None else str(v)) for k, v in fields.items()},
                auto_regenerate=False,
                flatten=True,
            )
        except Exception as exc:  # noqa: BLE001 — flatten support varies by pypdf
            logger.warning("flatten via update_page_form_field_values failed: %s", exc)
    return _writer_to_bytes(writer)


def generate_from_text(text: str, title: str | None = None) -> bytes:
    """Generate a simple multi-paragraph PDF from plain text.

    Blank lines separate paragraphs. An optional *title* is rendered as a
    heading. Uses reportlab's Platypus flowables with automatic page breaks.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    if text is None:
        text = ""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=(title or "Document"))
    styles = getSampleStyleSheet()
    story: list[Any] = []
    if title:
        story.append(Paragraph(escape(str(title)), styles["Title"]))
        story.append(Spacer(1, 12))
    paragraphs = str(text).split("\n\n")
    for para in paragraphs:
        # Preserve single line breaks inside a paragraph as <br/>.
        safe = escape(para.strip()).replace("\n", "<br/>")
        if not safe:
            story.append(Spacer(1, 12))
            continue
        story.append(Paragraph(safe, styles["BodyText"]))
        story.append(Spacer(1, 6))
    if not story:
        story.append(Paragraph("(empty)", styles["BodyText"]))
    try:
        doc.build(story)
    except Exception as exc:
        raise PdfError(f"Could not generate PDF: {exc}") from exc
    out = buf.getvalue()
    if not out.startswith(_PDF_MAGIC):
        raise PdfError("Internal error: generated bytes are not a valid PDF.")
    return out
