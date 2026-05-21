"""Wikilink + tag parser for LazyBrain notes.

Extracts [[Page Name]] references and #tag tokens from markdown.  Case- and
whitespace-normalises wikilink targets so "[[Redis]]" and "[[redis]]" resolve
to the same page.  Code fences and inline code spans are stripped first so we
don't mis-link code samples.

Supported wikilink shapes (Obsidian / Logseq compatible):
    [[Target]]                        → kind='wikilink'
    [[Target#Section]]                → kind='wikilink', anchor='Section'
    [[Target|Display Text]]           → kind='wikilink', display='Display Text'
    [[Target#Section|Display Text]]   → kind='wikilink'
    [[Target^block-id]]               → kind='block_ref', anchor='block-id'
    ![[Target]]                       → kind='embed' (Obsidian transclusion)
    ![[Target#Section]]               → kind='embed'

The backend regex is mirrored in ``web/src/lib/wikilink.ts``. If you change
either, change both.

``link_kind`` is the syntactic shape of the brackets; it is orthogonal to
``edge_type`` in ``note_links`` (which carries the semantic relation:
wikilink | supersedes | contradicts | references | derives_from). A single
edge can be ``edge_type='references'`` AND ``link_kind='embed'`` — i.e. an
embedded transclusion that the user later upgraded to a typed reference.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ![[…]] vs [[…]] is captured by the optional leading `!`. We must match
# both inline so `![[a]]` and `[[a]]` on the same line both fire — and so
# the embed sigil never gets eaten by the previous expression.
#
#   group 1 = '!' when embed, '' when plain wikilink
#   group 2 = target  (no [, ], newline, #, |, ^)
#   group 3 = block id when ``^`` is used (no [, ], newline, |, ^)
#   group 4 = anchor  (section, after #) (no [, ], newline, |)
#   group 5 = display (after |)          (no [, ], newline)
#
# Order: target → (^block-id OR #anchor) → |display. Block refs and
# section anchors are mutually exclusive at parse time — Obsidian treats
# ``[[X^id#sec]]`` as malformed and we follow suit.
_WIKILINK_RE = re.compile(
    r"(!)?\[\[("
    r"[^\[\]\n#|^]{1,120}"             # target
    r")"
    r"(?:\^([^\[\]\n|^]{1,80}))?"      # optional block id (^)
    r"(?:#([^\[\]\n|]{1,80}))?"        # optional section anchor (#)
    r"(?:\|([^\[\]\n]{1,120}))?"       # optional display
    r"\]\]"
)


# Syntactic shape literals — kept as module-level strings so call sites
# and tests can compare without re-importing magic constants.
KIND_WIKILINK = "wikilink"
KIND_EMBED = "embed"
KIND_BLOCK_REF = "block_ref"


@dataclass(frozen=True)
class Wikilink:
    """One parsed `[[target#anchor|display]]` occurrence (immutable).

    ``link_kind`` is the syntactic shape:
      * ``'wikilink'`` — plain ``[[X]]`` / ``[[X#sec]]`` / ``[[X|d]]``.
      * ``'embed'``    — Obsidian transclusion ``![[X]]`` (renders inline).
      * ``'block_ref'``— ``[[X^block-id]]`` (anchor holds the block id).
    """
    target: str          # normalized (lowercase, whitespace-collapsed) page key
    anchor: str          # raw (preserves case) — empty when absent
    display: str         # raw display override — empty when absent
    raw_target: str      # raw target before normalize — useful for round-trip
    link_kind: str = KIND_WIKILINK

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


def _classify_match(match: re.Match) -> tuple[str, str]:
    """Return ``(link_kind, anchor)`` from a regex match.

    ``link_kind`` is determined by the syntactic shape:
      * leading ``!``       → embed (transclusion);
      * ``^block-id``       → block_ref (anchor holds the block id);
      * default             → wikilink (anchor may hold a section name).
    """
    is_embed = bool(match.group(1))
    block_id = (match.group(3) or "").strip()
    section = (match.group(4) or "").strip()
    if block_id:
        # ``[[X^id]]`` — block ref overrides embed marker when both fire.
        # (Obsidian's renderer treats ``![[X^id]]`` as an embed of a block,
        # but the *edge* still points to a block; keep the kind as block_ref
        # so PPR can weight block-level reuse distinctly from page embeds.)
        return KIND_BLOCK_REF, block_id
    if is_embed:
        return KIND_EMBED, section
    return KIND_WIKILINK, section


def extract_wikilinks(markdown: str) -> list[str]:
    """Return the list of normalised wikilink targets in the markdown body.

    The original public API — pipe-alias / anchor extras are dropped here;
    callers that need them should use :func:`extract_wikilinks_full`.
    """
    clean = _strip_code(markdown)
    seen: list[str] = []
    seen_set: set[str] = set()
    for match in _WIKILINK_RE.finditer(clean):
        target = normalize_page(match.group(2))
        if target and target not in seen_set:
            seen.append(target)
            seen_set.add(target)
    return seen


def extract_wikilinks_full(markdown: str) -> list[Wikilink]:
    """Return parsed `Wikilink` records preserving anchor + display text.

    Deduped by ``(target, anchor, link_kind)`` so the same page referenced
    multiple ways (``[[X]]`` and ``![[X]]`` and ``[[X^block]]``) returns
    three distinct records — important so the link-graph can persist
    different syntactic edges. Callers that need a per-occurrence list
    (including duplicate identical tuples) should use
    :func:`extract_wikilinks_with_counts`.
    """
    clean = _strip_code(markdown)
    seen: list[Wikilink] = []
    seen_set: set[tuple[str, str, str]] = set()
    for match in _WIKILINK_RE.finditer(clean):
        raw_target = match.group(2) or ""
        target = normalize_page(raw_target)
        if not target:
            continue
        link_kind, anchor = _classify_match(match)
        key = (target, anchor, link_kind)
        if key in seen_set:
            continue
        seen.append(
            Wikilink(
                target=target,
                anchor=anchor,
                display=(match.group(5) or "").strip(),
                raw_target=raw_target.strip(),
                link_kind=link_kind,
            )
        )
        seen_set.add(key)
    return seen


def extract_wikilinks_with_counts(
    markdown: str,
) -> list[tuple[Wikilink, int]]:
    """Return ``[(Wikilink, occurrence_count)]`` preserving Obsidian-style
    link counts.

    The same ``(target, anchor, link_kind)`` tuple appearing N times in
    the body yields ONE entry with ``occurrence_count=N``. ``display`` is
    taken from the FIRST occurrence (later pipe aliases are ignored, same
    as Obsidian's backlink panel). Order matches first-occurrence order
    so the resulting rows stay stable across re-saves.
    """
    clean = _strip_code(markdown)
    by_key: dict[tuple[str, str, str], list] = {}
    order: list[tuple[str, str, str]] = []
    for match in _WIKILINK_RE.finditer(clean):
        raw_target = match.group(2) or ""
        target = normalize_page(raw_target)
        if not target:
            continue
        link_kind, anchor = _classify_match(match)
        key = (target, anchor, link_kind)
        if key not in by_key:
            by_key[key] = [
                Wikilink(
                    target=target,
                    anchor=anchor,
                    display=(match.group(5) or "").strip(),
                    raw_target=raw_target.strip(),
                    link_kind=link_kind,
                ),
                1,
            ]
            order.append(key)
        else:
            by_key[key][1] += 1
    return [(by_key[k][0], by_key[k][1]) for k in order]


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
        target_raw = match.group(2) or ""
        if normalize_page(target_raw) != old_key:
            return match.group(0)
        replacements += 1
        embed_prefix = match.group(1) or ""   # '!' for embed, '' for plain
        block_id = match.group(3) or ""       # ^block-id
        anchor = match.group(4) or ""         # #section
        display = match.group(5) or ""        # |display
        # Default rebuild: keep block id (^), anchor (#), explicit display.
        # Order mirrors the parser: target → ^block → #anchor → |display.
        suffix = ""
        if block_id:
            suffix += f"^{block_id}"
        if anchor:
            suffix += f"#{anchor}"
        if display:
            suffix += f"|{display}"
        elif preserve_display and raw_old_clean:
            # No explicit display — preserve the old surface text so reading
            # the rewritten body still "looks the same".
            suffix += f"|{raw_old_clean}"
        return f"{embed_prefix}[[{new}{suffix}]]"

    parts = _CODE_PART_RE.split(markdown)
    for idx, part in enumerate(parts):
        if idx % 2 == 0:  # non-code region
            parts[idx] = _WIKILINK_RE.sub(_substitute, part)
    return "".join(parts), replacements
