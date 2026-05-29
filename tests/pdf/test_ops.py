"""Tests for pure PDF operations (lazyclaw/pdf/ops.py).

Covers generate→page_count→extract round-trip, merge/split/rotate/delete,
AcroForm fields + fill, overlay/redaction non-corruption, flatten, and that
every output is a valid PDF (starts with b"%PDF"). No PyMuPDF/borb.
"""

from __future__ import annotations

import pytest

from lazyclaw.pdf import ops
from tests.pdf.conftest import make_form_pdf, make_multipage_pdf, make_text_pdf


def _is_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and bytes(b[:4]) == b"%PDF"


# ── generate / inspect ──────────────────────────────────────────────────────

def test_generate_is_valid_pdf(text_pdf):
    assert _is_pdf(text_pdf)


def test_generate_page_count(text_pdf):
    assert ops.page_count(text_pdf) == 1


def test_generate_extract_roundtrip():
    data = make_text_pdf("Unique sentinel phrase.\n\nAnother block.", title="Report")
    text = ops.extract_text(data)
    assert "Unique sentinel phrase" in text
    assert "Another block" in text


def test_generate_empty_text_still_valid():
    data = ops.generate_from_text("")
    assert _is_pdf(data)
    assert ops.page_count(data) == 1


def test_multipage_generation():
    data = make_multipage_pdf(3)
    assert ops.page_count(data) >= 3


def test_extract_tables_no_tables_returns_empty(text_pdf):
    assert ops.extract_tables(text_pdf) == []


def test_is_pdf_positive_and_negative(text_pdf):
    assert ops.is_pdf(text_pdf) is True
    assert ops.is_pdf(b"definitely not a pdf") is False
    assert ops.is_pdf(b"") is False


# ── merge ───────────────────────────────────────────────────────────────────

def test_merge_increases_page_count(text_pdf):
    merged = ops.merge([text_pdf, text_pdf, text_pdf])
    assert _is_pdf(merged)
    assert ops.page_count(merged) == 3


def test_merge_empty_list_raises():
    with pytest.raises(ops.PdfError):
        ops.merge([])


# ── split ───────────────────────────────────────────────────────────────────

def test_split_none_one_per_page(multipage_pdf):
    n = ops.page_count(multipage_pdf)
    parts = ops.split(multipage_pdf, None)
    assert len(parts) == n
    assert all(_is_pdf(p) and ops.page_count(p) == 1 for p in parts)


def test_split_ranges(multipage_pdf):
    parts = ops.split(multipage_pdf, [(1, 2)])
    assert len(parts) == 1
    assert ops.page_count(parts[0]) == 2


def test_split_out_of_bounds_raises(text_pdf):
    with pytest.raises(ops.PdfError):
        ops.split(text_pdf, [(1, 5)])  # only 1 page


# ── rotate ──────────────────────────────────────────────────────────────────

def test_rotate_all_pages(text_pdf):
    out = ops.rotate(text_pdf, 90)
    assert _is_pdf(out)
    assert ops.page_count(out) == ops.page_count(text_pdf)


def test_rotate_specific_page(multipage_pdf):
    out = ops.rotate(multipage_pdf, 180, pages=[1])
    assert _is_pdf(out)


def test_rotate_non_multiple_of_90_raises(text_pdf):
    with pytest.raises(ops.PdfError):
        ops.rotate(text_pdf, 45)


# ── delete pages ────────────────────────────────────────────────────────────

def test_delete_pages(multipage_pdf):
    n = ops.page_count(multipage_pdf)
    out = ops.delete_pages(multipage_pdf, [2])
    assert _is_pdf(out)
    assert ops.page_count(out) == n - 1


def test_delete_all_pages_refused(text_pdf):
    with pytest.raises(ops.PdfError):
        ops.delete_pages(text_pdf, [1])  # would delete the only page


def test_delete_empty_list_raises(text_pdf):
    with pytest.raises(ops.PdfError):
        ops.delete_pages(text_pdf, [])


# ── AcroForm fields + fill ──────────────────────────────────────────────────

def test_get_form_fields_on_form(form_pdf):
    fields = ops.get_form_fields(form_pdf)
    assert "full_name" in fields


def test_get_form_fields_on_plain_is_empty(text_pdf):
    assert ops.get_form_fields(text_pdf) == {}


def test_fill_form_sets_value(form_pdf):
    filled = ops.fill_form(form_pdf, {"full_name": "Alice Smith"})
    assert _is_pdf(filled)
    assert ops.get_form_fields(filled).get("full_name") == "Alice Smith"


def test_fill_form_on_plain_raises(text_pdf):
    with pytest.raises(ops.PdfError):
        ops.fill_form(text_pdf, {"x": "y"})


def test_fill_form_empty_values_raises(form_pdf):
    with pytest.raises(ops.PdfError):
        ops.fill_form(form_pdf, {})


# ── overlay / redaction (non-corruption) ────────────────────────────────────

def test_overlay_text_does_not_corrupt(text_pdf):
    out = ops.overlay_text(text_pdf, [{"page": 1, "x": 72, "y": 72, "text": "SIGNED"}])
    assert _is_pdf(out)
    assert ops.page_count(out) == ops.page_count(text_pdf)


def test_overlay_text_unknown_font_falls_back(text_pdf):
    out = ops.overlay_text(
        text_pdf, [{"page": 1, "x": 10, "y": 10, "text": "X", "font": "NoSuchFont"}]
    )
    assert _is_pdf(out)


def test_overlay_text_page_out_of_bounds_raises(text_pdf):
    with pytest.raises(ops.PdfError):
        ops.overlay_text(text_pdf, [{"page": 9, "x": 1, "y": 1, "text": "x"}])


def test_overlay_text_empty_items_raises(text_pdf):
    with pytest.raises(ops.PdfError):
        ops.overlay_text(text_pdf, [])


def test_redact_text_does_not_corrupt(text_pdf):
    out = ops.redact_text(text_pdf, 1, [(50, 700, 250, 720)])
    assert _is_pdf(out)
    assert ops.page_count(out) == ops.page_count(text_pdf)


def test_redact_text_bad_page_raises(text_pdf):
    with pytest.raises(ops.PdfError):
        ops.redact_text(text_pdf, 5, [(0, 0, 10, 10)])


# ── flatten ─────────────────────────────────────────────────────────────────

def test_flatten_form_keeps_value_extractable(form_pdf):
    filled = ops.fill_form(form_pdf, {"full_name": "Alice Smith"})
    flat = ops.flatten(filled)
    assert _is_pdf(flat)
    # the baked value is rendered into the page content
    assert "Alice" in ops.extract_text(flat)


def test_flatten_plain_pdf_is_noop_valid(text_pdf):
    out = ops.flatten(text_pdf)
    assert _is_pdf(out)


# ── bad input ───────────────────────────────────────────────────────────────

def test_page_count_on_garbage_raises():
    with pytest.raises(ops.PdfError):
        ops.page_count(b"not a pdf at all")
