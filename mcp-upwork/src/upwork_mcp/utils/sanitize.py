"""Plain-text sanitizers for content sent to Upwork form fields.

Upwork's cover-letter textarea (and screening-question textareas) are plain
text — they do NOT render Markdown. Anything `**bold**`, `[link](url)`,
fenced code blocks, headings, or bullet markers passed through verbatim
shows up as literal characters to the client and makes the proposal look
broken.

Mirrors `_strip_markdown` from
`lazyclaw/notifications/telegram_notifier.py:21-38` so mcp-upwork stays
standalone (no cross-package import).
"""

from __future__ import annotations

import re


_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Fenced code blocks first — preserve content but drop the fence lines.
    (re.compile(r"```[a-zA-Z0-9_-]*\n?", flags=re.MULTILINE), ""),
    # Bold: **text** or __text__
    (re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL), r"\1"),
    (re.compile(r"__(.+?)__", flags=re.DOTALL), r"\1"),
    # Italic: *text* or _text_  (run AFTER bold so we don't eat bold halves)
    (re.compile(r"\*(.+?)\*", flags=re.DOTALL), r"\1"),
    # Strikethrough: ~~text~~
    (re.compile(r"~~(.+?)~~", flags=re.DOTALL), r"\1"),
    # Inline code: `text`
    (re.compile(r"`(.+?)`", flags=re.DOTALL), r"\1"),
    # Headers: ###+ text → text  (line-anchored)
    (re.compile(r"^#{1,6}\s+", flags=re.MULTILINE), ""),
    # Links: [text](url) → text (url)
    (re.compile(r"\[(.+?)\]\((.+?)\)", flags=re.DOTALL), r"\1 (\2)"),
    # Bullet markers: leading "- " or "* " → "• "
    (re.compile(r"^[-*]\s+", flags=re.MULTILINE), "• "),
)


def strip_markdown_to_plaintext(text: str) -> str:
    """Convert Markdown text to a plain-text approximation suitable for Upwork.

    Idempotent — running the function twice on the same input produces the
    same output as running it once. The italic and bold patterns are written
    so a second pass over already-stripped text matches nothing.
    """
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
