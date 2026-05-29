"""Convert between the Univer ``IDocumentData`` snapshot and ``.docx`` / PDF.

The snapshot is LazyClaw's source of truth; ``.docx`` is a derived format
produced on export and parsed on import. PDF is a best-effort secondary export
that shells out to LibreOffice headless when it is available on the host.

python-docx (MIT) handles the ``.docx`` direction at the paragraph level — we
walk :func:`lazyclaw.docs.snapshot.get_paragraphs` on the way out and feed the
read-back paragraphs through :func:`lazyclaw.docs.snapshot.set_text` on the way
in, so the plain-text model round-trips cleanly.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess  # nosec B404 — fixed args, no shell, used for LibreOffice convert
import tempfile
from typing import Any

from docx import Document

from lazyclaw.docs import snapshot as D

logger = logging.getLogger(__name__)

# How long to give LibreOffice before giving up on a PDF conversion.
_SOFFICE_TIMEOUT_S = 60


def snapshot_to_docx(snap: dict[str, Any]) -> bytes:
    """Render a Univer document snapshot to ``.docx`` bytes.

    The first non-empty paragraph is emitted as a Heading 1 (a sensible title
    for an otherwise plain document); every remaining paragraph becomes a body
    paragraph. An empty document still produces a valid (empty) ``.docx``.
    """
    document = Document()
    paragraphs = D.get_paragraphs(snap)

    heading_used = False
    for para in paragraphs:
        text = "" if para is None else str(para)
        if not heading_used and text.strip():
            document.add_heading(text, level=1)
            heading_used = True
        else:
            document.add_paragraph(text)

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def docx_to_snapshot(data: bytes, name: str | None = None) -> dict[str, Any]:
    """Parse ``.docx`` bytes into a Univer document snapshot."""
    document = Document(io.BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs]
    # Drop a single trailing empty paragraph python-docx often emits.
    while paragraphs and paragraphs[-1] == "":
        paragraphs.pop()
    base = D.blank_document(name or "Imported")
    return D.set_text(base, "\n".join(paragraphs))


# ───────────────────────── PDF (best-effort) ────────────────────────

def _libreoffice_available() -> bool:
    """Return True when a LibreOffice headless binary is on the PATH."""
    return any(shutil.which(name) for name in ("soffice", "libreoffice"))


def _soffice_binary() -> str | None:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


def snapshot_to_pdf(snap: dict[str, Any]) -> bytes | None:
    """Best-effort PDF export via LibreOffice headless.

    Writes a temp ``.docx``, runs ``soffice --headless --convert-to pdf`` on
    it, and returns the resulting PDF bytes. Returns ``None`` (never raises)
    when LibreOffice is absent or the conversion fails for any reason — callers
    should treat ``None`` as "PDF unavailable on this host".
    """
    binary = _soffice_binary()
    if not binary:
        logger.debug("snapshot_to_pdf skipped: no LibreOffice binary on PATH")
        return None

    docx_bytes = snapshot_to_docx(snap)
    try:
        with tempfile.TemporaryDirectory() as workdir:
            docx_path = os.path.join(workdir, "doc.docx")
            with open(docx_path, "wb") as fh:
                fh.write(docx_bytes)
            result = subprocess.run(  # nosec B603 — fixed binary + args, no shell
                [
                    binary,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    workdir,
                    docx_path,
                ],
                cwd=workdir,
                capture_output=True,
                timeout=_SOFFICE_TIMEOUT_S,
                check=False,
            )
            pdf_path = os.path.join(workdir, "doc.pdf")
            if result.returncode != 0 or not os.path.exists(pdf_path):
                logger.warning(
                    "LibreOffice PDF conversion failed (rc=%s): %.200s",
                    result.returncode,
                    result.stderr.decode("utf-8", "replace") if result.stderr else "",
                )
                return None
            with open(pdf_path, "rb") as fh:
                return fh.read()
    except Exception as exc:  # noqa: BLE001 — best-effort, never propagate
        logger.warning("snapshot_to_pdf failed: %s", exc)
        return None
