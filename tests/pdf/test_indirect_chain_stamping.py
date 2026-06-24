"""Regression: stamping/overlay on PDFs with indirect-to-indirect object bodies.

pypdf 6.x made ``IndirectObject.indirect_reference`` a read-only property. Any
PDF whose object body is a *bare indirect reference* (e.g. ``4 0 obj  5 0 R``)
makes pypdf's reader/clone machinery attempt ``obj.indirect_reference = ...`` on
an ``IndirectObject`` while resolving/caching it, raising::

    AttributeError: property 'indirect_reference' of 'IndirectObject' object
    has no setter

In production this surfaced as
``doc_specialist[pdf] rejected by apply: Could not load PDF for stamping: ...``.

Real-world PDFs (some scanners / form generators) emit such indirect-to-indirect
bodies, so the toolkit must tolerate them. These tests build minimal but
*spec-valid* PDFs that carry the offending shape and exercise the stamping path
end to end.
"""

from __future__ import annotations

import io

import pytest

from lazyclaw.pdf import ops


def _build_pdf_with_indirect_chain(obj4_body: bytes = b"5 0 R") -> bytes:
    """A 1-page PDF whose page ``/Contents`` (object 4) is a *bare* indirect ref.

    Object 4's body is ``obj4_body`` (default ``5 0 R``) — i.e. object 4 is an
    indirect reference to object 5 (the real content stream). This is the
    indirect-to-indirect shape that trips pypdf's read-only
    ``indirect_reference`` property. Cross-reference offsets are computed so the
    file parses without falling back to xref reconstruction.
    """
    objs: dict[int, bytes] = {}
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    objs[3] = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 6 0 R >> >> >>"
    )
    objs[4] = obj4_body  # bare indirect reference -> the actual content stream
    stream = b"BT /F1 24 Tf 72 720 Td (Hello chained) Tj ET"
    objs[5] = (
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream"
    )
    objs[6] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n")
    xref_pos = out.tell()
    size = max(objs) + 1
    out.write(f"xref\n0 {size}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for num in range(1, size):
        out.write(f"{offsets[num]:010d} 00000 n \n".encode())
    out.write(b"trailer\n")
    out.write(f"<< /Size {size} /Root 1 0 R >>\n".encode())
    out.write(b"startxref\n")
    out.write(f"{xref_pos}\n".encode())
    out.write(b"%%EOF\n")
    return out.getvalue()


@pytest.fixture
def indirect_chain_pdf() -> bytes:
    return _build_pdf_with_indirect_chain()


def test_overlay_text_on_indirect_chain_pdf(indirect_chain_pdf):
    """overlay_text must not raise the indirect_reference-setter error."""
    out = ops.overlay_text(
        indirect_chain_pdf,
        [{"page": 1, "x": 72, "y": 72, "text": "SIGNED"}],
    )
    assert out.startswith(b"%PDF")
    # Still a readable, single-page PDF after stamping.
    assert ops.page_count(out) == 1


def test_redact_on_indirect_chain_pdf(indirect_chain_pdf):
    """redact_text shares the same _merge_overlay path and must survive too."""
    out = ops.redact_text(indirect_chain_pdf, 1, [(50, 700, 250, 720)])
    assert out.startswith(b"%PDF")
    assert ops.page_count(out) == 1


def test_reader_handles_indirect_chain_inspection(indirect_chain_pdf):
    """Basic inspection (page_count / extract_text) must not crash either."""
    assert ops.page_count(indirect_chain_pdf) == 1
    # The content stream is reachable through the collapsed chain.
    assert "Hello chained" in ops.extract_text(indirect_chain_pdf)


def test_overlay_on_dangling_indirect_chain():
    """A chain whose target is missing degrades gracefully (no crash)."""
    pdf = _build_pdf_with_indirect_chain(obj4_body=b"99 0 R")  # object 99 absent
    out = ops.overlay_text(pdf, [{"page": 1, "x": 10, "y": 10, "text": "X"}])
    assert out.startswith(b"%PDF")
    assert ops.page_count(out) == 1


def test_overlay_on_self_referential_indirect_chain():
    """A self-referential body (4 0 obj  4 0 R) must not infinite-loop."""
    pdf = _build_pdf_with_indirect_chain(obj4_body=b"4 0 R")
    out = ops.overlay_text(pdf, [{"page": 1, "x": 10, "y": 10, "text": "X"}])
    assert out.startswith(b"%PDF")
    assert ops.page_count(out) == 1
