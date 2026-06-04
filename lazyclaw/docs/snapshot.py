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
import re
from typing import Any
from uuid import uuid4

# Univer's paragraph terminator and body terminator.
PARAGRAPH_BREAK = "\r"
SECTION_BREAK = "\n"

# Univer's custom-range sentinels. A hyperlink (or any custom range) wraps its
# span of ``dataStream`` between these two control characters; the range's
# ``startIndex`` points at CUSTOM_RANGE_START and ``endIndex`` at
# CUSTOM_RANGE_END (both inclusive). See @univerjs/core DataStreamTreeTokenType.
CUSTOM_RANGE_START = "\u001f"
CUSTOM_RANGE_END = "\u001e"

# CustomRangeType.HYPERLINK in @univerjs/core (i-document-data.d.ts).
_HYPERLINK = 0


def _strip_tokens(s: str) -> str:
    """Remove the custom-range sentinel chars from a visible-text slice."""
    return s.replace(CUSTOM_RANGE_START, "").replace(CUSTOM_RANGE_END, "")


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
            out.append(_strip_tokens(stream[prev:end]))
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
    return [_strip_tokens(p) for p in body.split(PARAGRAPH_BREAK)]


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


# ───────────────────────── rich runs (hyperlinks) ───────────────────
#
# A "run" is the unit of inline content: ``{"text": str}`` for plain text or
# ``{"text": str, "url": str}`` for a hyperlink. A paragraph is a list of runs.
# These helpers carry hyperlinks through the same immutable model the plain-text
# helpers use, by emitting Univer ``customRanges`` bracketed in ``dataStream``
# with the CUSTOM_RANGE_START / CUSTOM_RANGE_END sentinels.

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def _clean_run_text(text: str) -> str:
    """Sanitise run text: drop paragraph/section breaks and stray sentinels."""
    return (
        str(text)
        .replace(PARAGRAPH_BREAK, " ")
        .replace(SECTION_BREAK, " ")
        .replace(CUSTOM_RANGE_START, "")
        .replace(CUSTOM_RANGE_END, "")
    )


def build_body_with_runs(
    paragraphs: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build a Univer ``body`` from paragraphs-of-runs, emitting hyperlinks.

    Each paragraph is a list of runs; a run with a truthy ``url`` becomes a
    hyperlink ``customRange`` whose span in ``dataStream`` is wrapped with the
    sentinel tokens. An empty paragraph (``[]``) collapses to a bare ``"\\r"``.
    """
    if not paragraphs:
        paragraphs = [[]]

    stream_parts: list[str] = []
    para_meta: list[dict[str, Any]] = []
    custom_ranges: list[dict[str, Any]] = []
    cursor = 0
    link_counter = 0

    for runs in paragraphs:
        for run in runs or []:
            if not isinstance(run, dict):
                continue
            text = _clean_run_text(run.get("text", ""))
            url = run.get("url")
            if url:
                start_token = cursor
                stream_parts.append(CUSTOM_RANGE_START)
                cursor += 1
                stream_parts.append(text)
                cursor += len(text)
                end_token = cursor
                stream_parts.append(CUSTOM_RANGE_END)
                cursor += 1
                custom_ranges.append(
                    {
                        "startIndex": start_token,
                        "endIndex": end_token,
                        "rangeId": f"link-{link_counter}",
                        "rangeType": _HYPERLINK,
                        "properties": {"url": str(url)},
                    }
                )
                link_counter += 1
            else:
                stream_parts.append(text)
                cursor += len(text)
        stream_parts.append(PARAGRAPH_BREAK)
        para_meta.append({"startIndex": cursor})
        cursor += 1

    data_stream = "".join(stream_parts) + SECTION_BREAK
    return {
        "dataStream": data_stream,
        "paragraphs": para_meta,
        "textRuns": [],
        "customRanges": custom_ranges,
        "sectionBreaks": [{"startIndex": len(data_stream) - 1}],
    }


def _paragraph_spans(stream: str) -> list[tuple[int, int]]:
    """Absolute ``(start, end)`` char spans per paragraph (end = the ``\\r``).

    Scans ``dataStream`` for paragraph terminators, which is robust regardless
    of whether ``paragraphs`` metadata is present/consistent and yields the
    absolute indices the hyperlink ``customRanges`` are expressed in.
    """
    spans: list[tuple[int, int]] = []
    prev = 0
    for idx, ch in enumerate(stream):
        if ch == PARAGRAPH_BREAK:
            spans.append((prev, idx))
            prev = idx + 1
    return spans


def get_paragraph_runs(snap: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Return paragraphs as lists of runs, reconstructing hyperlinks.

    Inverse of :func:`build_body_with_runs`. An empty paragraph yields ``[]``;
    plain text yields a single ``{"text": …}`` run; hyperlink spans become
    ``{"text": …, "url": …}`` runs in document order.
    """
    body = snap.get("body") if isinstance(snap, dict) else None
    if not isinstance(body, dict):
        return []
    stream = body.get("dataStream")
    if not isinstance(stream, str) or stream == "":
        return []

    start_map: dict[int, tuple[int, str]] = {}
    for c in body.get("customRanges") or []:
        if not isinstance(c, dict):
            continue
        s, e = c.get("startIndex"), c.get("endIndex")
        props = c.get("properties")
        url = props.get("url") if isinstance(props, dict) else None
        if isinstance(s, int) and isinstance(e, int) and url:
            start_map[s] = (e, str(url))

    result: list[list[dict[str, Any]]] = []
    for p_start, p_end in _paragraph_spans(stream):
        runs: list[dict[str, Any]] = []
        plain: list[str] = []
        i = p_start
        while i < p_end:
            ch = stream[i]
            if ch == CUSTOM_RANGE_START and i in start_map:
                end_idx, url = start_map[i]
                if plain:
                    runs.append({"text": "".join(plain)})
                    plain = []
                runs.append({"text": _strip_tokens(stream[i + 1 : end_idx]), "url": url})
                i = end_idx + 1
            elif ch in (CUSTOM_RANGE_START, CUSTOM_RANGE_END):
                i += 1  # stray sentinel — skip
            else:
                plain.append(ch)
                i += 1
        if plain:
            runs.append({"text": "".join(plain)})
        result.append(runs)
    return result


def append_paragraph_with_runs(
    snap: dict[str, Any], runs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return a NEW snapshot with one paragraph (list of runs) appended.

    A blank document is replaced by the new paragraph (matching
    :func:`append_paragraph`); existing hyperlinks are preserved.
    """
    existing = get_paragraph_runs(snap) if isinstance(snap, dict) else []
    if get_paragraphs(snap) in ([""], []):
        new_paras = [list(runs)]
    else:
        new_paras = [*existing, list(runs)]
    out = copy.deepcopy(snap) if isinstance(snap, dict) else {}
    if "id" not in out:
        out["id"] = f"doc-{uuid4().hex[:12]}"
    out.setdefault("documentStyle", {})
    out["body"] = build_body_with_runs(new_paras)
    return out


def runs_from_markdown(text: str) -> list[dict[str, Any]]:
    """Parse inline markdown ``[label](url)`` links into a run list."""
    text = str(text or "")
    runs: list[dict[str, Any]] = []
    pos = 0
    for m in _MD_LINK_RE.finditer(text):
        if m.start() > pos:
            runs.append({"text": text[pos : m.start()]})
        runs.append({"text": m.group(1), "url": m.group(2)})
        pos = m.end()
    if pos < len(text):
        runs.append({"text": text[pos:]})
    return runs or [{"text": text}]


def make_link_runs(
    text: str, link_text: str | None, url: str | None
) -> list[dict[str, Any]]:
    """Build runs for ``text`` where ``link_text`` is linked to ``url``.

    Splits ``text`` around the first occurrence of ``link_text``; if absent,
    the link is appended to the end. With no ``url``/``link_text``, returns a
    single plain run.
    """
    text = str(text or "")
    if not url or not link_text:
        return [{"text": text}]
    link_text = str(link_text)
    idx = text.find(link_text)
    if idx == -1:
        runs: list[dict[str, Any]] = []
        if text:
            runs.append({"text": text if text.endswith(" ") else text + " "})
        runs.append({"text": link_text, "url": str(url)})
        return runs
    runs = []
    before, after = text[:idx], text[idx + len(link_text) :]
    if before:
        runs.append({"text": before})
    runs.append({"text": link_text, "url": str(url)})
    if after:
        runs.append({"text": after})
    return runs
