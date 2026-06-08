"""Tests for the markdown→blocks parser (Task A1)."""

from lazyclaw.docs.markdown_blocks import parse_blocks, runs_from_inline


def test_heading_levels():
    blocks = parse_blocks("# Title\n## Sub\n### Small")
    assert [b["type"] for b in blocks] == ["heading", "heading", "heading"]
    assert [b["level"] for b in blocks] == [1, 2, 3]
    assert blocks[0]["runs"] == [{"text": "Title"}]


def test_bullet_and_number_lists():
    blocks = parse_blocks("- one\n- two\n1. first\n2. second")
    assert [b["type"] for b in blocks] == ["bullet", "bullet", "number", "number"]
    assert blocks[2]["runs"] == [{"text": "first"}]


def test_number_list_paren_form():
    blocks = parse_blocks("1) a\n2) b")
    assert [b["type"] for b in blocks] == ["number", "number"]


def test_inline_bold_italic_link():
    runs = runs_from_inline("plain **bold** and *italic* and [site](https://x.io)")
    assert {"text": "bold", "bold": True} in runs
    assert {"text": "italic", "italic": True} in runs
    assert {"text": "site", "url": "https://x.io"} in runs


def test_underscore_italic():
    runs = runs_from_inline("a _em_ b")
    assert {"text": "em", "italic": True} in runs


def test_plain_paragraph_passthrough():
    blocks = parse_blocks("just a line")
    assert blocks == [
        {"type": "paragraph", "level": 0, "runs": [{"text": "just a line"}]}
    ]


def test_malformed_never_raises():
    blocks = parse_blocks("**oops and [bad](nope")
    assert blocks[0]["type"] == "paragraph"
    assert "oops" in blocks[0]["runs"][0]["text"]


def test_empty_input_yields_one_empty_paragraph():
    assert parse_blocks("") == [
        {"type": "paragraph", "level": 0, "runs": [{"text": ""}]}
    ]
