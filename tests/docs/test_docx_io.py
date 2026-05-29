"""Tests for the docx/pdf I/O layer (lazyclaw/docs/docx_io.py).

Covers: snapshot→docx produces a valid OOXML zip, docx→snapshot round-trips
the plain text, and the PDF best-effort guard never raises when LibreOffice is
absent.
"""

from __future__ import annotations

from lazyclaw.docs import docx_io
from lazyclaw.docs import snapshot as D


def test_snapshot_to_docx_is_a_zip():
    doc = D.set_text(D.blank_document("Letter"), "Title line\nBody one\nBody two")
    data = docx_io.snapshot_to_docx(doc)
    assert data[:2] == b"PK"  # .docx is a zip container
    assert len(data) > 0


def test_snapshot_to_docx_empty_doc():
    data = docx_io.snapshot_to_docx(D.blank_document("Empty"))
    assert data[:2] == b"PK"


def test_docx_round_trip_preserves_text():
    original = "Heading goes here\nFirst body paragraph.\nSecond body paragraph."
    doc = D.set_text(D.blank_document("Letter"), original)
    data = docx_io.snapshot_to_docx(doc)
    back = docx_io.docx_to_snapshot(data, name="Reimported")
    assert D.get_text(back) == original


def test_docx_to_snapshot_names_the_doc():
    data = docx_io.snapshot_to_docx(D.set_text(D.blank_document("X"), "hi"))
    back = docx_io.docx_to_snapshot(data, name="MyName")
    assert back["name"] == "MyName"


def test_libreoffice_guard_returns_bool():
    assert isinstance(docx_io._libreoffice_available(), bool)


def test_snapshot_to_pdf_never_raises():
    # Whether or not LibreOffice is installed, this must return bytes or None,
    # never raise.
    doc = D.set_text(D.blank_document("Letter"), "Some content")
    result = docx_io.snapshot_to_pdf(doc)
    assert result is None or isinstance(result, (bytes, bytearray))


def test_snapshot_to_pdf_none_when_no_libreoffice(monkeypatch):
    monkeypatch.setattr(docx_io, "_soffice_binary", lambda: None)
    assert docx_io.snapshot_to_pdf(D.blank_document("X")) is None
