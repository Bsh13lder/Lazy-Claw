"""Wikilink + tag parser for LazyBrain notes.

Extracts [[Page Name]] references and #tag tokens from markdown.  Case- and
whitespace-normalises wikilink targets so "[[Redis]]" and "[[redis]]" resolve
to the same page.  Code fences and inline code spans are stripped first so we
don't mis-link code samples.
"""
from __future__ import annotations

import re

# [[Target]] — allows letters, digits, spaces, slashes, dashes, dots, parens
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]{1,120})\]\]")

# #tag — starts at word boundary, supports / for hierarchies (#site/whatsapp)
_TAG_RE = re.compile(r"(?:(?<=\s)|(?<=^))#([A-Za-z][A-Za-z0-9_/\-]{0,63})")

# Strip fenced code blocks ```...``` and inline `code`
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _strip_code(markdown: str) -> str:
    return _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", markdown))


def normalize_page(name: str) -> str:
    """Case-fold and collapse whitespace so wikilinks resolve consistently."""
    return " ".join(name.strip().lower().split())


def extract_wikilinks(markdown: str) -> list[str]:
    """Return the list of normalised wikilink targets in the markdown body."""
    clean = _strip_code(markdown)
    seen: list[str] = []
    seen_set: set[str] = set()
    for match in _WIKILINK_RE.finditer(clean):
        target = normalize_page(match.group(1))
        if target and target not in seen_set:
            seen.append(target)
            seen_set.add(target)
    return seen


def extract_tags(markdown: str) -> list[str]:
    """Return the list of normalised #tags in the markdown body."""
    clean = _strip_code(markdown)
    seen: list[str] = []
    seen_set: set[str] = set()
    for match in _TAG_RE.finditer(clean):
        tag = match.group(1).lower()
        if tag and tag not in seen_set:
            seen.append(tag)
            seen_set.add(tag)
    return seen


def parse(markdown: str) -> tuple[list[str], list[str]]:
    """Shortcut: (wikilinks, tags)."""
    return extract_wikilinks(markdown), extract_tags(markdown)
