"""Parse a small, safe subset of Markdown into structured document *blocks*.

A **block** is the unit the rich-formatting pipeline speaks::

    {"type": "heading"|"paragraph"|"bullet"|"number", "level": int,
     "runs": [run, ...]}

A **run** is ``{"text": str, "bold"?: bool, "italic"?: bool,
"underline"?: bool, "url"?: str}``.

This module is pure text→dict shaping with NO Univer knowledge —
:mod:`lazyclaw.docs.snapshot` turns blocks into the Univer wire form. Malformed
markup degrades to plain text and NEVER raises (input-validation rule: never
trust external/agent-authored content).
"""

from __future__ import annotations

import re
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_NUMBER_RE = re.compile(r"^\d+[.)]\s+(.*)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
# Single-* or single-_ italic, not greedy across ** and not matching empty.
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)|(?<!_)_([^_]+)_(?!_)")


def runs_from_inline(text: str) -> list[dict[str, Any]]:
    """Split one line into styled runs (bold / italic / link).

    Scans left-to-right for the earliest of a link, bold, or italic span and
    emits the plain text before it as its own run. Anything unmatched (and any
    malformed markup) stays plain. Always returns at least one run.
    """
    text = str(text or "")
    runs: list[dict[str, Any]] = []
    pos = 0
    while pos < len(text):
        nxt = _earliest_match(text, pos)
        if nxt is None:
            runs.append({"text": text[pos:]})
            break
        kind, m = nxt
        if m.start() > pos:
            runs.append({"text": text[pos : m.start()]})
        if kind == "link":
            runs.append({"text": m.group(1), "url": m.group(2)})
        elif kind == "bold":
            runs.append({"text": m.group(1), "bold": True})
        else:  # italic — group(1) is the * form, group(2) the _ form
            runs.append({"text": m.group(1) or m.group(2), "italic": True})
        pos = m.end()
    merged = [r for r in runs if r.get("text") != ""]
    return merged or [{"text": text}]


def _earliest_match(text: str, pos: int):
    """Return ``(kind, match)`` for the earliest inline span at/after ``pos``."""
    candidates = []
    for kind, rx in (("link", _LINK_RE), ("bold", _BOLD_RE), ("italic", _ITALIC_RE)):
        m = rx.search(text, pos)
        if m:
            candidates.append((m.start(), kind, m))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    _, kind, m = candidates[0]
    return kind, m


def parse_blocks(text: str) -> list[dict[str, Any]]:
    """Parse multi-line markdown into a list of blocks (one block per line).

    Recognises ``#``/``##``/``###`` headings, ``-``/``*`` bullets, ``1.``/``1)``
    numbered items, and inline emphasis/links; everything else is a paragraph.
    An empty string yields a single empty paragraph.
    """
    blocks: list[dict[str, Any]] = []
    for raw in str(text or "").split("\n"):
        line = raw.rstrip("\r")
        h = _HEADING_RE.match(line)
        if h:
            blocks.append(
                {"type": "heading", "level": len(h.group(1)),
                 "runs": runs_from_inline(h.group(2))}
            )
            continue
        b = _BULLET_RE.match(line)
        if b:
            blocks.append(
                {"type": "bullet", "level": 0, "runs": runs_from_inline(b.group(1))}
            )
            continue
        n = _NUMBER_RE.match(line)
        if n:
            blocks.append(
                {"type": "number", "level": 0, "runs": runs_from_inline(n.group(1))}
            )
            continue
        blocks.append(
            {"type": "paragraph", "level": 0, "runs": runs_from_inline(line)}
        )
    return blocks or [{"type": "paragraph", "level": 0, "runs": [{"text": ""}]}]
