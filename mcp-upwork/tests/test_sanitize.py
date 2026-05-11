"""Tests for utils.sanitize.strip_markdown_to_plaintext.

Upwork's cover-letter textarea is plain text. Anything `**bold**`,
`[link](url)`, fenced code blocks, or markdown headers passes through
verbatim and the client sees raw characters. The sanitizer is the
boundary defense — these tests pin its behaviour.
"""

from upwork_mcp.utils.sanitize import strip_markdown_to_plaintext


def test_strips_double_star_bold() -> None:
    assert strip_markdown_to_plaintext("**LazyClaw** rocks") == "LazyClaw rocks"


def test_strips_underscore_bold() -> None:
    assert strip_markdown_to_plaintext("__important__ note") == "important note"


def test_strips_single_star_italic() -> None:
    assert strip_markdown_to_plaintext("*emphasis* matters") == "emphasis matters"


def test_strips_strikethrough() -> None:
    assert strip_markdown_to_plaintext("~~old~~ text") == "old text"


def test_strips_inline_code() -> None:
    assert strip_markdown_to_plaintext("run `pytest -v`") == "run pytest -v"


def test_strips_h1_through_h6_headers() -> None:
    src = "# H1\n## H2\n### H3\n#### H4\n##### H5\n###### H6"
    expected = "H1\nH2\nH3\nH4\nH5\nH6"
    assert strip_markdown_to_plaintext(src) == expected


def test_rewrites_link_to_text_and_url() -> None:
    src = "Check [GitHub](https://github.com/Bsh13lder/Lazy-Claw)."
    expected = "Check GitHub (https://github.com/Bsh13lder/Lazy-Claw)."
    assert strip_markdown_to_plaintext(src) == expected


def test_converts_dash_bullets_to_unicode() -> None:
    src = "- item one\n- item two\n* item three"
    expected = "• item one\n• item two\n• item three"
    assert strip_markdown_to_plaintext(src) == expected


def test_strips_fenced_code_block_fences_keeps_content() -> None:
    src = "Run this:\n```python\nprint('hi')\n```\nDone."
    out = strip_markdown_to_plaintext(src)
    assert "```" not in out
    assert "print('hi')" in out


def test_idempotent_on_real_proposal_sample() -> None:
    """The exact text Vato sent ended up with literal ** in the Upwork inbox.

    Running the sanitizer twice must yield the same result as running
    it once — guards against the italic regex accidentally eating bold
    halves on a second pass.
    """
    src = (
        "Hi,\n\n"
        "Your task will be executed by **LazyClaw** — an encrypted AI agent "
        "I'm developing ([GitHub](https://github.com/Bsh13lder/Lazy-Claw)). "
        "I'm using **Claude's latest models (Opus 4.7 & Sonnet 4.6)** with "
        "human-in-the-loop oversight.\n\n"
        "**What I'll build:**\n\n"
        "1. **Telegram Bot Interface** — Clock-in/out, shift notes\n"
        "2. **Manufacturing Data Ingestion** — REST API / CSV / DB\n\n"
        "**Why LazyClaw:**\n"
        "- 10+ Telegram bots built\n"
        "- Manufacturing data processing experience\n\n"
        "**Offer:** €20 fixed for the full bot build\n\n"
        "Best,\n**Vato**"
    )
    once = strip_markdown_to_plaintext(src)
    twice = strip_markdown_to_plaintext(once)
    assert once == twice, "sanitizer must be idempotent"
    assert "**" not in once, f"residual ** in: {once!r}"
    assert "[GitHub]" not in once
    assert "GitHub (https://github.com/Bsh13lder/Lazy-Claw)" in once
    assert "• 10+ Telegram bots built" in once
    assert "Vato" in once and "**Vato**" not in once


def test_empty_string_passes_through() -> None:
    assert strip_markdown_to_plaintext("") == ""


def test_plain_text_passes_through_unchanged() -> None:
    src = "Just a normal sentence with no markdown.\nSecond line."
    assert strip_markdown_to_plaintext(src) == src


def test_url_alone_unchanged() -> None:
    """Bare URLs (which Upwork auto-linkifies in cover letters) stay bare."""
    src = "GitHub: https://github.com/Bsh13lder/Lazy-Claw"
    assert strip_markdown_to_plaintext(src) == src
