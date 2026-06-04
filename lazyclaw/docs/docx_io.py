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
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from lazyclaw.docs import snapshot as D

logger = logging.getLogger(__name__)

# How long to give LibreOffice before giving up on a PDF conversion.
_SOFFICE_TIMEOUT_S = 60


def _add_hyperlink(paragraph, text: str, url: str) -> None:
    """Append a real ``w:hyperlink`` run to a python-docx paragraph.

    python-docx has no hyperlink API, so we build the OOXML element directly:
    relate the paragraph's part to the external ``url`` and wrap a run in a
    ``w:hyperlink`` carrying that relationship id. Styled blue + underlined so
    it reads as a link.
    """
    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rpr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(underline)
    run.append(rpr)

    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def snapshot_to_docx(snap: dict[str, Any]) -> bytes:
    """Render a Univer document snapshot to ``.docx`` bytes.

    The first non-empty all-plain paragraph is emitted as a Heading 1 (a
    sensible title for an otherwise plain document); every remaining paragraph
    becomes a body paragraph. Runs carrying a ``url`` are written as real
    ``w:hyperlink`` elements. An empty document still produces a valid ``.docx``.
    """
    document = Document()
    paragraphs = D.get_paragraph_runs(snap)

    heading_used = False
    for runs in paragraphs:
        has_link = any(r.get("url") for r in runs)
        text = "".join(r.get("text", "") for r in runs)
        if not heading_used and text.strip() and not has_link:
            document.add_heading(text, level=1)
            heading_used = True
            continue
        para = document.add_paragraph()
        for run in runs:
            url = run.get("url")
            if url:
                _add_hyperlink(para, run.get("text", ""), str(url))
            else:
                para.add_run(run.get("text", ""))

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _docx_paragraph_runs(paragraph) -> list[dict[str, Any]]:
    """Extract ordered runs (plain + hyperlink) from a python-docx paragraph."""
    runs: list[dict[str, Any]] = []
    rels = paragraph.part.rels
    for child in paragraph._p:
        tag = child.tag
        if tag == qn("w:r"):
            text = "".join(node.text or "" for node in child.findall(qn("w:t")))
            if text:
                runs.append({"text": text})
        elif tag == qn("w:hyperlink"):
            r_id = child.get(qn("r:id"))
            url = None
            if r_id and r_id in rels:
                url = rels[r_id].target_ref
            text = "".join(
                node.text or "" for node in child.iter(qn("w:t"))
            )
            if text:
                runs.append({"text": text, "url": url} if url else {"text": text})
    return runs


def docx_to_snapshot(data: bytes, name: str | None = None) -> dict[str, Any]:
    """Parse ``.docx`` bytes into a Univer document snapshot (links preserved)."""
    document = Document(io.BytesIO(data))
    paragraphs = [_docx_paragraph_runs(p) for p in document.paragraphs]
    # Drop a single trailing empty paragraph python-docx often emits.
    while paragraphs and not paragraphs[-1]:
        paragraphs.pop()
    base = D.blank_document(name or "Imported")
    if not paragraphs:
        return base
    out = {**base, "body": D.build_body_with_runs(paragraphs)}
    return out


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
