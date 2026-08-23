"""Pure, dependency-free renderers for the Telegram outbound visual language.

Every function takes plain data and returns strings. No telegram / lazyclaw
domain imports — keeps this trivially unit-testable and lets the same renderers
back a future structured ``view_hint`` pipeline (spec approach B).

Design: 2026-06-22-telegram-chat-visual-upgrade-design.md
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

PARSE_MODE = "HTML"
DIVIDER = "─" * 9  # ─────────

_ANCHOR_RE = re.compile(r'<a\s+href="([^"]*)">([^<]*)</a>')


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_html(text: str) -> str:
    """HTML-escape ``text`` for Telegram HTML mode, preserving ``<a href>`` links.

    Unlike the old ``_prepare_html``, the href value AND the link text are
    escaped (an unescaped ``&`` inside an href made Telegram reject the message).
    """
    # Strip NUL bytes up front so literal input can never collide with the
    # \x00LINK sentinel used to stash links during escaping.
    text = text.replace("\x00", "")
    placeholders: list[str] = []

    def _save(m: "re.Match[str]") -> str:
        href = _esc(m.group(1))
        label = _esc(m.group(2))
        placeholders.append(f'<a href="{href}">{label}</a>')
        return f"\x00LINK{len(placeholders) - 1}\x00"

    text = _ANCHOR_RE.sub(_save, text)
    text = _esc(text)
    for i, link in enumerate(placeholders):
        text = text.replace(f"\x00LINK{i}\x00", link)
    return text


def html_to_plain(text: str) -> str:
    """Reverse ``escape_html`` for the plain-text fallback path.

    Turns ``<a href="u">t</a>`` into ``t (u)`` and unescapes entities, so a
    Telegram HTML ``BadRequest`` can be retried as readable plain text instead
    of a dropped message.
    """
    text = _ANCHOR_RE.sub(r"\2 (\1)", text)
    return (
        text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    )


@dataclass(frozen=True)
class FooterMeta:
    """Inputs for the reply footer."""

    model_label: str | None = None
    tool_count: int = 0
    elapsed_s: float = 0.0
    fallback_reason: str | None = None


@dataclass(frozen=True)
class Step:
    """A single line in the live status card."""

    label: str
    state: Literal["done", "active", "pending"] = "active"


def _safe_cut(s: str, limit: int) -> int:
    """Return a cut index ``<= limit`` for HTML-escaped ``s`` that does not
    split an entity (``&...;``) or a tag (``<...>``). Falls back to ``limit``
    if backing off would make zero progress (a single token longer than limit).
    """
    if len(s) <= limit:
        return len(s)
    cut = limit
    # Back out of an unterminated entity: a '&' with no ';' before the cut.
    amp = s.rfind("&", 0, cut)
    if amp != -1 and s.find(";", amp, cut) == -1:
        cut = amp
    # Back out of an unterminated tag: a '<' with no '>' before the cut.
    lt = s.rfind("<", 0, cut)
    if lt != -1 and s.find(">", lt, cut) == -1:
        cut = min(cut, lt)
    return cut if cut > 0 else limit


def chunk_message(body: str, footer: str, limit: int = 4096) -> list[str]:
    """Split ``body`` into Telegram-safe chunks; append footer to the LAST chunk.

    Guarantees: every chunk is ``<= limit``, the footer appears ONLY on the
    final chunk and is NEVER dropped, and (for HTML-escaped input) no cut lands
    inside an entity or tag. Fixes the original overflow (a ~4000-char last
    chunk + footer blew past 4096) AND the footer-drop bug where a final
    segment landing in ``(last_budget, limit]`` silently lost the footer.
    """
    suffix = f"\n\n{DIVIDER}\n{footer}" if footer else ""
    if len(body) + len(suffix) <= limit:
        return [body + suffix]

    # Body must be chunked. The LAST chunk has to fit body-slice + suffix.
    last_budget = max(1, limit - len(suffix))
    chunks: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        remaining = n - i
        # If what's left fits in a final chunk WITH the suffix, take it all.
        if remaining <= last_budget:
            chunks.append(body[i:] + suffix)
            return chunks
        # NOT the final chunk. Cap the slice so it never exceeds limit AND so a
        # remainder that would otherwise have no footer room is left enough for
        # a valid final chunk: when only `last_budget < remaining <= limit`
        # is left, slicing `last_budget` (not limit) reserves footer room.
        budget = limit if remaining > limit else last_budget
        cut = _safe_cut(body[i:], budget)
        chunks.append(body[i : i + cut])
        i += cut
    return chunks


_FALLBACK_REASONS = {
    "overloaded": "Sonnet overloaded",
    "auth": "auth error",
    "cli_failed": "CLI failed",
    "local_failed": "local model failed",
    "worker_failed": "worker failed",
}


def render_footer(meta: FooterMeta) -> str:
    """Lean ``· ``-separated footer. Preserves the fallback chip signal."""
    parts: list[str] = []
    if meta.tool_count:
        unit = "tool" if meta.tool_count == 1 else "tools"
        parts.append(f"{meta.tool_count} {unit}")
    parts.append(f"{meta.elapsed_s:.1f}s")
    if meta.fallback_reason:
        label = meta.model_label or "?"
        reason = _FALLBACK_REASONS.get(meta.fallback_reason, meta.fallback_reason)
        parts.append(f"⚠️ fallback → {label} ({reason})")
    elif meta.model_label:
        parts.insert(0, meta.model_label)
    return "· " + " · ".join(parts)


def render_reply(body: str, footer: str) -> str:
    """Body + divider + footer. Caller is responsible for HTML-escaping body."""
    if not footer:
        return body
    return f"{body}\n\n{DIVIDER}\n{footer}"


_ERROR_CARDS = {
    "brain": (
        "⚠️ Quick hiccup\n"
        "My brain stalled for a sec and couldn't finish that. "
        "Give it another go.\n"
        "   ↳ /status"
    ),
    "rate_limit": (
        "⚠️ I'm rate-limited right now\n"
        "Retry in a minute, or set a fallback model with /mode so this "
        "re-routes automatically."
    ),
    "content_policy": (
        "🛡️ That request was declined by the model's safeguards\n"
        "It read as a security-sensitive topic. For legitimate automation of "
        "systems you own or administer, the sanctioned path is Anthropic's "
        "cyber-use-case exemption (per-org, ~2 business days):\n"
        "   ↳ https://claude.com/form/cyber-use-case"
    ),
    "generic": (
        "⚠️ Something went wrong on my end.\n"
        "Try again in a moment.\n"
        "   ↳ /status for details"
    ),
}

_STEP_GLYPH = {"done": "✓", "active": "⟳", "pending": "·"}


def render_error(
    kind: Literal["brain", "rate_limit", "content_policy", "generic"],
) -> str:
    """Friendly error card. Accepts a closed enum — never raw exception text,
    so leaking internals into chat is structurally impossible."""
    return _ERROR_CARDS.get(kind, _ERROR_CARDS["generic"])


def render_status(header: str, steps: list[Step]) -> str:
    """Live ``⚙️``-style status card, edited in place by the caller."""
    lines = [f"⚙️ {header}"]
    for s in steps:
        glyph = _STEP_GLYPH.get(s.state, "·")
        suffix = "…" if s.state == "active" else ""
        lines.append(f"  {glyph} {s.label}{suffix}")
    return "\n".join(lines)
