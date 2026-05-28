"""Sanitize on-demand recall hits so paraphrased summaries can't be mimicked
as live channel quotes.

The leak path being closed (2026-05-24 investigation):

Daily-log / session-log notes contain pre-formatted strings like

    **James Blue** (10:37 PM):
    > spec spec spec...

When the brain calls ``lazybrain_recall_typed_memory("James Blue")`` or
``lazybrain_semantic_search("james upwork")``, those paraphrased blocks
come back verbatim. Opus+cache then mimics them in its reply as if they
were a live ``upwork_get_conversation`` tool result — surfacing a
contact "quote" the contact never actually wrote.

Existing defenses don't cover this path:
  - ``quarantine_polluted_history`` filters *assistant* messages only.
  - ``_find_wikilink_in_quote_block`` runs on the brain's *output* draft,
    after the leak already shaped the reply.
  - ``is_auto_inject_type`` filters the *cached layer* but not the
    on-demand recall surface.

This module is the missing piece: when a recall skill is about to
return content from a paraphrase-class note, it wraps the content with
explicit "NOT a live quote" framing AND mangles the sender-timestamp
patterns so the brain literally cannot lift them character-for-character
as if they were a fresh channel read.

Pure functions. No I/O. No state. Safe to call anywhere.
"""

from __future__ import annotations

import re

# Authoritative-state memory types — the user wrote these directly
# (preferences, decisions, external pointers). They do NOT contain
# paraphrased channel quotes, so the sanitizer passes them through
# unchanged. EVERYTHING ELSE (session-log, fact, other, NULL, unknown
# custom types from forward-compat callers) is treated as paraphrase-
# class and sanitized — fail closed.
_AUTHORITATIVE_TYPES: frozenset[str] = frozenset({
    "user",
    "feedback",
    "project",
    "reference",
    "fact",
})


def is_paraphrase_class(memory_type: str | None) -> bool:
    """True iff ``memory_type`` should be sanitized as paraphrased content.

    Fail-closed: anything that ISN'T explicitly on the authoritative
    allowlist (``user`` / ``feedback`` / ``project`` / ``reference``)
    is treated as paraphrase — including NULL, empty string, and
    unknown custom values from forward-compat writers. The cost of
    over-sanitizing an authoritative note is small (a wrapper that
    says "not a live quote"); the cost of under-sanitizing a paraphrase
    is the leak this whole module exists to close.
    """
    if not memory_type:
        return True
    return memory_type not in _AUTHORITATIVE_TYPES


# ─── Sender-timestamp pattern stripping ──────────────────────────────────
#
# The patterns the brain has been observed mimicking from paraphrase notes:
#
#   1. Bold-sender bold-colon:   ``**James Blue (10:37 PM):**`` line prefix
#   2. Bold-sender plain-colon:  ``**James Blue** (10:37 PM):`` (sender bold only)
#   3. Plain-sender:             ``James Blue (10:37 PM):`` line prefix
#   4. Blockquote-bold-sender:   ``> **James Blue** (10:37 PM):`` quote line
#   5. Plain blockquote:         ``> James Blue (10:37 PM): content``
#
# The mangler replaces each with ``[paraphrased: <sender> @ <ts>]`` so
# the line still carries the WHO and WHEN information (useful context),
# but the brain can't lift it verbatim as a fresh ``> sender (ts):`` line
# in its reply — the F1 verifier will catch any reply that tries.
#
# Timestamps inside parens that look like ``HH:MM (AM|PM)?`` or
# ``HH:MM:SS (AM|PM)?``. Tight pattern — won't match parenthetical
# years or other numerics.

_TIMESTAMP_PATTERN = (
    r"\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?(?:\s+[A-Z]{2,4})?"
)

# Shape 1: ``**Sender Name (ts):**`` — entire bold block, both sender and
# timestamp inside the bold markers, colon at the end inside bold.
_BOLD_BLOCK_RE = re.compile(
    r"(?:^|\n)\s*(?:>\s*)?\*\*"
    r"(?P<sender>[^*\n]{1,80}?)\s*\("
    r"(?P<ts>" + _TIMESTAMP_PATTERN + r")"
    r"\)\s*:\*\*",
    re.MULTILINE,
)

# Shape 2: ``**Sender** (ts):`` — bold sender + plain parenthesized ts + colon.
_BOLD_SENDER_PLAIN_TS_RE = re.compile(
    r"(?:^|\n)\s*(?:>\s*)?\*\*"
    r"(?P<sender>[^*\n]{1,80}?)\*\*"
    r"\s*\((?P<ts>" + _TIMESTAMP_PATTERN + r")\)\s*:",
    re.MULTILINE,
)

# Shape 3: Plain ``Sender (ts):`` at line start (with optional ``>``
# blockquote prefix). Tight enough that ordinary prose like
# ``met John (yesterday)`` won't match — requires the parenthesized
# group to match the HH:MM timestamp shape.
_PLAIN_SENDER_RE = re.compile(
    r"(?:^|\n)\s*(?:>\s*)?"
    r"(?P<sender>[A-Z][\w \-\.']{1,60})\s*\("
    r"(?P<ts>" + _TIMESTAMP_PATTERN + r")"
    r"\)\s*:",
    re.MULTILINE,
)


def _replace_with_paraphrase_marker(match: re.Match) -> str:
    sender = (match.group("sender") or "").strip()
    ts = (match.group("ts") or "").strip()
    # Preserve a leading newline if the match captured one so we don't
    # collapse two paragraphs into one.
    matched = match.group(0)
    leading_nl = "\n" if matched.startswith("\n") else ""
    return f"{leading_nl}[paraphrased: {sender} @ {ts}]"


def strip_sender_timestamp_patterns(text: str) -> str:
    """Replace embedded sender-timestamp blocks with ``[paraphrased: …]``.

    Applies in priority order: bold-block → bold-sender + plain-ts →
    plain. Earlier shapes are STRICT supersets of later ones so the
    cascading replace doesn't double-mangle.

    Pure: input string never mutated, NEVER raises. A regex that fails
    on a pathological input is treated as a no-op for that pattern.
    """
    if not text:
        return text
    out = text
    for pattern in (_BOLD_BLOCK_RE, _BOLD_SENDER_PLAIN_TS_RE, _PLAIN_SENDER_RE):
        try:
            out = pattern.sub(_replace_with_paraphrase_marker, out)
        except Exception:  # noqa: BLE001 — sanitizer must never crash recall
            continue
    return out


# ─── Framing wrap ─────────────────────────────────────────────────────────


_WRAP_HEADER: str = (
    "[CACHED PARAPHRASE — this is a daily-log / session summary, "
    "NOT a live channel quote. The (HH:MM) timestamps inside are "
    "paraphrased markers, not verbatim. To quote the contact, call "
    "the channel-read tool directly.]"
)

_WRAP_FOOTER: str = "[END CACHED PARAPHRASE]"


def wrap_paraphrase(content: str, *, memory_type: str | None = None,
                    title: str | None = None) -> str:
    """Wrap ``content`` with explicit paraphrase framing.

    Idempotent: if ``content`` already starts with the wrap header, it
    is returned unchanged. The framing tells the brain:
      1. this is NOT a live channel read,
      2. embedded ``(HH:MM)`` markers are paraphrased — don't lift them
         as if they were verbatim quotes,
      3. the path to verbatim is the channel-read tool.

    Pure: never raises, never mutates.
    """
    if not content:
        return content
    if content.lstrip().startswith("[CACHED PARAPHRASE"):
        return content
    label_bits = []
    if memory_type:
        label_bits.append(f"type={memory_type}")
    if title:
        label_bits.append(f"title={title!r}")
    label = (" (" + ", ".join(label_bits) + ")") if label_bits else ""
    return f"{_WRAP_HEADER}{label}\n{content}\n{_WRAP_FOOTER}"


# ─── Public API used by recall skills ────────────────────────────────────


def sanitize_recall_content(
    content: str | None,
    memory_type: str | None,
    *,
    title: str | None = None,
) -> str:
    """One-call sanitizer for recall-skill formatters.

    For paraphrase-class notes (``session-log`` / ``fact`` / ``other`` /
    NULL): strip embedded sender-timestamp blocks AND wrap with framing.

    For authoritative-state notes (``user`` / ``feedback`` / ``project``
    / ``reference``): return content unchanged. These were authored
    directly and don't contain the mimic surface.

    The two-stage transform is the load-bearing guard:
      - Stripping alone leaves the body bare; the brain might still
        treat the bullets as conversation excerpts.
      - Wrapping alone leaves the ``**Sender (HH:MM):**`` patterns
        intact for the brain to lift verbatim.
      - Together they make it impossible to produce a forged quote
        line that survives the F1 detector AND looks plausible.
    """
    if content is None:
        return ""
    if not is_paraphrase_class(memory_type):
        return content
    stripped = strip_sender_timestamp_patterns(content)
    return wrap_paraphrase(stripped, memory_type=memory_type, title=title)
