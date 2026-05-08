"""Wikilink + tag parser for LazyBrain notes.

Extracts [[Page Name]] references and #tag tokens from markdown.  Case- and
whitespace-normalises wikilink targets so "[[Redis]]" and "[[redis]]" resolve
to the same page.  Code fences and inline code spans are stripped first so we
don't mis-link code samples.

Supported wikilink shapes (Obsidian / Logseq compatible):
    [[Target]]
    [[Target#Section]]
    [[Target|Display Text]]
    [[Target#Section|Display Text]]
    ![[Target]]                       (transclusion — handled by callers)

The backend regex is mirrored in ``web/src/lib/wikilink.ts``. If you change
either, change both.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# [[Target#Anchor|Display]] — only Target is required.
#   group 1 = target  (no [, ], newline, #, |)
#   group 2 = anchor  (optional; no [, ], newline, |)
#   group 3 = display (optional; no [, ], newline)
_WIKILINK_RE = re.compile(
    r"\[\[("
    r"[^\[\]\n#|]{1,120}"          # target
    r")"
    r"(?:#([^\[\]\n|]{1,80}))?"    # optional anchor
    r"(?:\|([^\[\]\n]{1,120}))?"   # optional display
    r"\]\]"
)


@dataclass(frozen=True)
class Wikilink:
    """One parsed `[[target#anchor|display]]` occurrence (immutable)."""
    target: str          # normalized (lowercase, whitespace-collapsed) page key
    anchor: str          # raw (preserves case) — empty when absent
    display: str         # raw display override — empty when absent
    raw_target: str      # raw target before normalize — useful for round-trip

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
    """Return the list of normalised wikilink targets in the markdown body.

    The original public API — pipe-alias / anchor extras are dropped here;
    callers that need them should use :func:`extract_wikilinks_full`.
    """
    clean = _strip_code(markdown)
    seen: list[str] = []
    seen_set: set[str] = set()
    for match in _WIKILINK_RE.finditer(clean):
        target = normalize_page(match.group(1))
        if target and target not in seen_set:
            seen.append(target)
            seen_set.add(target)
    return seen


def extract_wikilinks_full(markdown: str) -> list[Wikilink]:
    """Return parsed `Wikilink` records preserving anchor + display text.

    Deduped by ``target`` only (so ``[[X|alias-A]]`` and ``[[X|alias-B]]``
    return one record — the first occurrence wins). Callers that need every
    occurrence (including duplicates) should use the regex directly.
    """
    clean = _strip_code(markdown)
    seen: list[Wikilink] = []
    seen_set: set[str] = set()
    for match in _WIKILINK_RE.finditer(clean):
        raw_target = match.group(1) or ""
        target = normalize_page(raw_target)
        if not target or target in seen_set:
            continue
        seen.append(
            Wikilink(
                target=target,
                anchor=(match.group(2) or "").strip(),
                display=(match.group(3) or "").strip(),
                raw_target=raw_target.strip(),
            )
        )
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


# Code-fence-aware splitter: capturing group so re.split keeps the delimiters.
# Even-indexed chunks are plain markdown; odd-indexed are code (skip them).
_CODE_PART_RE = re.compile(r"(```.*?```|`[^`\n]+`)", re.DOTALL)


def rewrite_wikilink_target(
    markdown: str,
    old: str,
    new: str,
    *,
    preserve_display: bool = True,
) -> tuple[str, int]:
    """Rewrite ``[[old]]`` → ``[[new]]`` (and ``[[old#a|d]]`` → ``[[new#a|d]]``)
    in plain markdown regions.

    Matches are case-insensitive on the target (uses ``normalize_page`` so
    ``[[Redis]]`` and ``[[redis]]`` both rewrite). Wikilinks inside code
    fences or inline-code spans are left untouched, matching the rule used
    by :func:`extract_wikilinks`.

    Anchor + display segments are preserved verbatim across the rewrite, so
    ``[[old#api|API docs]]`` becomes ``[[new#api|API docs]]``.

    When ``preserve_display`` is True (the default — Obsidian "Smart Rename"
    behaviour) AND the matched link has no explicit ``|display`` AND the new
    target differs from the old in any way other than case-fold, we insert
    the previously-visible surface text as a pipe alias so backlink display
    text doesn't lurch when the file is renamed::

        [[Project Atlas]]            old name visible
        ─ rename Project Atlas → Atlas v2 ─
        [[Atlas v2|Project Atlas]]    backlink still reads "Project Atlas"

    Returns ``(new_markdown, replacement_count)``. When ``old`` is empty or
    no match is found, the input markdown is returned unchanged with count 0.
    """
    if not markdown:
        return markdown, 0
    old_key = normalize_page(old)
    if not old_key:
        return markdown, 0

    new_key = normalize_page(new)
    # Identity rename — nothing to do (avoids inserting a useless self-alias).
    if new_key == old_key:
        return markdown, 0

    replacements = 0
    raw_old_clean = (old or "").strip()

    def _substitute(match: re.Match) -> str:
        nonlocal replacements
        target_raw = match.group(1) or ""
        if normalize_page(target_raw) != old_key:
            return match.group(0)
        replacements += 1
        anchor = match.group(2) or ""
        display = match.group(3) or ""
        # Default rebuild: keep anchor + explicit display.
        suffix = ""
        if anchor:
            suffix += f"#{anchor}"
        if display:
            suffix += f"|{display}"
        elif preserve_display and raw_old_clean:
            # No explicit display — preserve the old surface text so reading
            # the rewritten body still "looks the same".
            suffix += f"|{raw_old_clean}"
        return f"[[{new}{suffix}]]"

    parts = _CODE_PART_RE.split(markdown)
    for idx, part in enumerate(parts):
        if idx % 2 == 0:  # non-code region
            parts[idx] = _WIKILINK_RE.sub(_substitute, part)
    return "".join(parts), replacements
