"""Unit tests for the Telegram outbound visual language (telegram_view)."""
import dataclasses

import pytest

from lazyclaw.channels.telegram_view import (
    DIVIDER,
    FooterMeta,
    Step,
    chunk_message,
    escape_html,
    html_to_plain,
    render_error,
    render_footer,
    render_reply,
    render_status,
)


# ── escape_html ──────────────────────────────────────────────────────────

def test_escape_html_escapes_entities():
    assert escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_escape_html_preserves_anchor_but_escapes_ampersand_in_href():
    # the _prepare_html bug: & inside href was left raw → Telegram rejected it
    out = escape_html('see <a href="http://x/?a=1&b=2">link</a> now')
    assert '<a href="http://x/?a=1&amp;b=2">link</a>' in out
    assert out.startswith("see ")


def test_html_to_plain_reverses_escape_and_anchors():
    html = escape_html('go <a href="http://x/?a=1&b=2">here</a> now & wait')
    plain = html_to_plain(html)
    assert plain == "go here (http://x/?a=1&b=2) now & wait"


def test_footermeta_and_step_are_frozen():
    m = FooterMeta(model_label="Opus 4.8", tool_count=3, elapsed_s=2.1)
    s = Step(label="read inbox", state="done")
    assert dataclasses.is_dataclass(m) and dataclasses.is_dataclass(s)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.tool_count = 9  # type: ignore[misc]


# ── chunk_message ────────────────────────────────────────────────────────

def test_chunk_message_short_single_chunk():
    out = chunk_message("hello", "footer", limit=4096)
    assert out == [f"hello\n\n{DIVIDER}\nfooter"]


def test_chunk_message_never_exceeds_limit_with_long_footer():
    body = "x" * 9000
    footer = "f" * 90
    chunks = chunk_message(body, footer, limit=4096)
    assert all(len(c) <= 4096 for c in chunks), [len(c) for c in chunks]
    # footer appears only on the last chunk
    assert footer in chunks[-1]
    assert not any(footer in c for c in chunks[:-1])
    # body fully preserved (strip the footer block off the last chunk)
    last_body = chunks[-1].split(f"\n\n{DIVIDER}\n")[0]
    assert "".join(chunks[:-1]) + last_body == body


def test_chunk_message_boundary_near_4000_final_chunk():
    # regression: old code put a near-4000 chunk + footer over 4096
    body = "y" * 4000
    chunks = chunk_message(body, "f" * 80, limit=4096)
    assert all(len(c) <= 4096 for c in chunks)


def test_chunk_message_no_footer():
    body = "z" * 5000
    chunks = chunk_message(body, "", limit=4096)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == body


def test_chunk_message_footer_never_dropped_at_boundary():
    # Regression (#4): a final segment landing in (last_budget, limit] used to
    # be emitted as a footer-less `limit`-sized chunk, silently dropping the
    # footer. body=limit with a real footer hits exactly that window.
    footer = "F" * 10
    chunks = chunk_message("x" * 100, footer, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert footer in chunks[-1], "footer was dropped on the boundary case"
    assert not any(footer in c for c in chunks[:-1])
    # body fully preserved
    last_body = chunks[-1].split(f"\n\n{DIVIDER}\n")[0]
    assert "".join(chunks[:-1]) + last_body == "x" * 100


def test_chunk_message_does_not_split_html_entity():
    # Regression (#3): a cut must not land inside an `&...;` entity.
    body = ("a" * 78) + "&amp;" + ("b" * 60)
    chunks = chunk_message(body, "", limit=80)
    assert all(len(c) <= 80 for c in chunks)
    assert "".join(chunks) == body
    for c in chunks:
        # every '&' in an escaped chunk must keep its terminating ';'
        idx = c.find("&")
        while idx != -1:
            assert ";" in c[idx:idx + 6], f"chunk split an entity: ...{c[idx:idx+6]!r}"
            idx = c.find("&", idx + 1)


def test_chunk_message_does_not_split_anchor_tag():
    body = ("a" * 70) + '<a href="http://example.com/x">link</a>' + ("b" * 60)
    chunks = chunk_message(body, "", limit=80)
    assert all(len(c) <= 80 for c in chunks)
    assert "".join(chunks) == body
    # no chunk ends in the middle of a tag (unterminated '<')
    for c in chunks:
        lt = c.rfind("<")
        if lt != -1:
            assert ">" in c[lt:], f"chunk split a tag: ...{c[lt:]!r}"


def test_escape_html_strips_nul_sentinel_collision():
    # Regression (#5): literal \x00LINK0\x00 in input must not be rewritten
    # into a saved anchor.
    out = escape_html('\x00LINK0\x00 and <a href="http://x">y</a>')
    assert '<a href="http://x">y</a>' in out
    assert "LINK0" in out  # the literal text survives as text, not a link


# ── render_footer / render_reply ─────────────────────────────────────────

def test_render_footer_basic():
    out = render_footer(FooterMeta(model_label="Opus 4.8", tool_count=3, elapsed_s=2.14))
    assert out == "· Opus 4.8 · 3 tools · 2.1s"


def test_render_footer_singular_tool_and_no_model():
    out = render_footer(FooterMeta(model_label=None, tool_count=1, elapsed_s=0.9))
    assert out == "· 1 tool · 0.9s"


def test_render_footer_fallback_chip():
    out = render_footer(FooterMeta(model_label="Haiku", tool_count=0, elapsed_s=3.0,
                                   fallback_reason="overloaded"))
    assert "⚠️ fallback → Haiku (Sonnet overloaded)" in out


def test_render_reply_assembles_body_divider_footer():
    out = render_reply("the answer", "· Opus 4.8 · 2.1s")
    assert out == f"the answer\n\n{DIVIDER}\n· Opus 4.8 · 2.1s"


def test_render_reply_no_footer_returns_body():
    assert render_reply("just this", "") == "just this"


# ── render_error / render_status ─────────────────────────────────────────

_LEAK_TOKENS = ["Traceback", "Exception", "SDK", "iterator",
                "Claude Code returned", "error result: success"]


@pytest.mark.parametrize("kind", ["brain", "rate_limit", "generic"])
def test_render_error_never_leaks_internals(kind):
    out = render_error(kind)
    for tok in _LEAK_TOKENS:
        assert tok not in out
    assert out  # non-empty


def test_render_error_kinds_are_distinct():
    assert len({render_error("brain"), render_error("rate_limit"),
                render_error("generic")}) == 3


def test_render_status_lists_steps_with_glyphs():
    out = render_status("Working", [Step("read inbox", "done"),
                                    Step("searching jobs", "active")])
    assert "⚙️ Working" in out
    assert "✓ read inbox" in out
    assert "⟳ searching jobs" in out
