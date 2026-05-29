"""Pure helpers over Univer's ``IDocumentData`` snapshot.

A LazyClaw doc is stored as one JSON blob in Univer's native document format.
These functions read and **immutably** edit that structure so the agent skills
(:mod:`lazyclaw.skills.builtin.docs`) and the docx layer
(:mod:`lazyclaw.docs.docx_io`) share a single text model. No I/O, no Univer
runtime dependency — just dict shaping.

Univer document shape (only the parts we touch)::

    {
      "id": "doc-…",
      "documentStyle": { "pageSize": {...}, ... },
      "body": {
        "dataStream": "Hello\\rWorld\\r\\n",
        "paragraphs": [{"startIndex": 5}, {"startIndex": 11}],
        "textRuns": [...],
        "sectionBreaks": [{"startIndex": 12}]
      }
    }

CONVENTION (Univer's on-the-wire form):
- In ``dataStream`` each paragraph ends with a ``"\\r"`` (carriage return);
  the whole body ends with a single ``"\\n"`` (the section-break sentinel).
- Each entry in ``body.paragraphs`` carries ``startIndex`` = the index of that
  paragraph's terminating ``"\\r"`` inside ``dataStream``.
- The text of paragraph *i* is the run of characters between the previous
  paragraph's ``"\\r"`` (exclusive) and this paragraph's ``"\\r"`` (exclusive).

Every mutator returns a NEW snapshot (deepcopy) — callers never observe
in-place changes (project immutability rule). Readers are robust to malformed
input (missing keys, wrong types) and degrade to an empty document.
"""

from __future__ import annotations

import copy
from typing import Any
from uuid import uuid4

# Univer's paragraph terminator and body terminator.
PARAGRAPH_BREAK = "\r"
SECTION_BREAK = "\n"


# ───────────────────────── document factory ─────────────────────────

def blank_document(name: str, *, doc_id: str | None = None) -> dict[str, Any]:
    """Build a minimal valid empty Univer ``IDocumentData``.

    An empty doc is a single empty paragraph: ``dataStream`` is ``"\\r\\n"``
    (one paragraph break + the section terminator) with one paragraph whose
    ``startIndex`` points at the ``"\\r"`` (index 0). ``name`` is carried for
    convenience even though Univer keys docs by ``id``; the store uses the
    plaintext column for the sidebar.

    ``doc_id`` defaults to a fresh uuid; tests may pass a fixed one.
    """
    did = doc_id or f"doc-{uuid4().hex[:12]}"
    return {
        "id": did,
        "name": (name or "Untitled doc").strip() or "Untitled doc",
        "documentStyle": {},
        "body": {
            "dataStream": PARAGRAPH_BREAK + SECTION_BREAK,
            "paragraphs": [{"startIndex": 0}],
            "textRuns": [],
            "sectionBreaks": [{"startIndex": 1}],
        },
    }


# ───────────────────────── internal builders ────────────────────────

def _build_body(paragraphs: list[str]) -> dict[str, Any]:
    """Construct a valid ``body`` dict from a list of paragraph strings.

    Each paragraph contributes ``"<text>\\r"`` to ``dataStream``; the body is
    terminated with a single ``"\\n"``. ``startIndex`` for each paragraph is
    the index of its ``"\\r"``. An empty list collapses to one empty paragraph
    (matching :func:`blank_document`).
    """
    if not paragraphs:
        paragraphs = [""]

    stream_parts: list[str] = []
    para_meta: list[dict[str, Any]] = []
    cursor = 0
    for text in paragraphs:
        # Defensive: never let a stray "\r"/"\n" inside the text corrupt the
        # paragraph index bookkeeping.
        clean = str(text).replace(PARAGRAPH_BREAK, " ").replace(SECTION_BREAK, " ")
        stream_parts.append(clean)
        stream_parts.append(PARAGRAPH_BREAK)
        cursor += len(clean)
        para_meta.append({"startIndex": cursor})
        cursor += 1  # account for the "\r" we just appended

    data_stream = "".join(stream_parts) + SECTION_BREAK
    return {
        "dataStream": data_stream,
        "paragraphs": para_meta,
        "textRuns": [],
        "sectionBreaks": [{"startIndex": len(data_stream) - 1}],
    }


# ───────────────────────── text read ────────────────────────────────

def get_paragraphs(snap: dict[str, Any]) -> list[str]:
    """Return the document's paragraphs as a list of plain strings.

    Robust to malformed input: a missing/empty body yields ``[]``; a body with
    a ``dataStream`` but no usable ``paragraphs`` metadata falls back to
    splitting the stream on ``"\\r"`` (dropping the trailing section break).
    """
    body = snap.get("body") if isinstance(snap, dict) else None
    if not isinstance(body, dict):
        return []
    stream = body.get("dataStream")
    if not isinstance(stream, str) or stream == "":
        return []

    paras = body.get("paragraphs")
    if isinstance(paras, list) and paras:
        out: list[str] = []
        prev = 0
        for p in paras:
            if not isinstance(p, dict):
                continue
            end = p.get("startIndex")
            if not isinstance(end, int) or end < prev or end > len(stream):
                # Malformed index — bail to the stream-split fallback.
                return _split_stream(stream)
            out.append(stream[prev:end])
            prev = end + 1  # skip the "\r"
        return out

    return _split_stream(stream)


def _split_stream(stream: str) -> list[str]:
    """Fallback: derive paragraphs purely from ``dataStream`` text.

    Mirrors :func:`_build_body`'s convention: every paragraph is terminated by
    a ``"\\r"`` and the body ends with a single ``"\\n"``. We drop the trailing
    section break, then drop the final paragraph terminator that sits right
    before it (so a well-formed ``"a\\rb\\r\\n"`` yields ``["a", "b"]``, not a
    spurious trailing empty paragraph), and split the remainder on ``"\\r"``.
    """
    body = stream[:-1] if stream.endswith(SECTION_BREAK) else stream
    if body.endswith(PARAGRAPH_BREAK):
        body = body[:-1]
    if body == "":
        return [""]
    return body.split(PARAGRAPH_BREAK)


def get_text(snap: dict[str, Any]) -> str:
    """Join the document's paragraphs into plain text with ``"\\n"``."""
    return "\n".join(get_paragraphs(snap))


def is_empty(snap: dict[str, Any]) -> bool:
    """True when the document has no non-whitespace content."""
    return get_text(snap).strip() == ""


# ───────────────────────── text write (immutable) ───────────────────

def set_text(snap: dict[str, Any], text: str) -> dict[str, Any]:
    """Return a NEW snapshot whose body is rebuilt from ``text``.

    The text is split on ``"\\n"`` into paragraphs; ``dataStream`` and
    ``paragraphs`` are regenerated. Top-level keys (``id``, ``name``,
    ``documentStyle``) are preserved. ``textRuns`` are reset (we don't carry
    rich formatting through a plain-text rewrite).
    """
    out = copy.deepcopy(snap) if isinstance(snap, dict) else {}
    if "id" not in out:
        out["id"] = f"doc-{uuid4().hex[:12]}"
    out.setdefault("documentStyle", {})
    paragraphs = (text or "").split("\n") if text is not None else [""]
    out["body"] = _build_body(paragraphs)
    return out


def append_paragraph(snap: dict[str, Any], text: str) -> dict[str, Any]:
    """Return a NEW snapshot with ``text`` appended as one new paragraph.

    A blank document (single empty paragraph) is replaced by the new
    paragraph rather than gaining a leading empty line — matching what a user
    expects when they "add a line" to a fresh doc.
    """
    paras = get_paragraphs(snap)
    if paras == [""] or paras == []:
        paras = [str(text)]
    else:
        paras = [*paras, str(text)]
    out = copy.deepcopy(snap) if isinstance(snap, dict) else {}
    if "id" not in out:
        out["id"] = f"doc-{uuid4().hex[:12]}"
    out.setdefault("documentStyle", {})
    out["body"] = _build_body(paras)
    return out
