"""Shared fixtures + tiny in-test PDF builders for the PDF toolkit tests.

All test PDFs are generated in-process — no binary fixtures on disk. Plain
documents come from ``ops.generate_from_text``; an AcroForm document is built
with reportlab's ``canvas.acroForm`` (a ``showPage()`` before ``save()`` is
required so the field's page reference resolves).
"""

from __future__ import annotations

import io

import pytest

from lazyclaw.pdf import ops


def make_text_pdf(text: str = "Hello world.\n\nSecond paragraph.", title: str | None = None) -> bytes:
    """A simple text PDF via the production generator."""
    return ops.generate_from_text(text, title=title)


def make_multipage_pdf(n_pages: int = 3) -> bytes:
    """A PDF guaranteed to span at least *n_pages* pages."""
    # ~60 paragraphs comfortably overflow one US-letter page; scale by pages.
    paras = "\n\n".join(
        f"Paragraph number {i} with some filler text to fill the page."
        for i in range(60 * n_pages)
    )
    data = ops.generate_from_text(paras)
    # Sanity: caller-visible guarantee.
    assert ops.page_count(data) >= n_pages
    return data


def make_form_pdf(field_name: str = "full_name") -> bytes:
    """An AcroForm PDF with a single text field."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(50, 750, "Name:")
    c.acroForm.textfield(
        name=field_name, x=120, y=740, width=200, height=20, borderStyle="inset"
    )
    c.showPage()  # finalize so the field's /P page reference resolves
    c.save()
    return buf.getvalue()


@pytest.fixture
def text_pdf() -> bytes:
    return make_text_pdf()


@pytest.fixture
def multipage_pdf() -> bytes:
    return make_multipage_pdf(3)


@pytest.fixture
def form_pdf() -> bytes:
    return make_form_pdf()
