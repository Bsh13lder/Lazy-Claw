"""Round-trip + edge cases for the Python frontmatter mirror.

The TS counterpart at web/src/components/lazybrain/frontmatter.ts is the
contract. Anything we serialize here must parse back unchanged, AND must
parse cleanly in the React panel — this test file pins the Python side.
"""
from __future__ import annotations

import pytest

from lazyclaw.lazybrain.frontmatter import (
    merge_frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
)


def test_parse_no_frontmatter_returns_body_unchanged():
    body = "Just a plain note body.\n\nMultiple paragraphs."
    props, out_body, has_fm = parse_frontmatter(body)
    assert props == {}
    assert out_body == body
    assert has_fm is False


def test_parse_empty_string():
    props, body, has_fm = parse_frontmatter("")
    assert props == {}
    assert body == ""
    assert has_fm is False


def test_parse_basic_scalars():
    raw = (
        "---\n"
        "kind: shape\n"
        "outcome: pending\n"
        "replay_count: 7\n"
        "is_active: true\n"
        "is_archived: false\n"
        "score: 3.14\n"
        "note: ~\n"
        "---\n"
        "Body here."
    )
    props, body, has_fm = parse_frontmatter(raw)
    assert has_fm is True
    assert props == {
        "kind": "shape",
        "outcome": "pending",
        "replay_count": 7,
        "is_active": True,
        "is_archived": False,
        "score": 3.14,
        "note": None,
    }
    assert body == "Body here."


def test_parse_flow_array():
    raw = "---\ntags: [foo, bar, baz]\n---\nBody"
    props, body, has_fm = parse_frontmatter(raw)
    assert has_fm is True
    assert props == {"tags": ["foo", "bar", "baz"]}
    assert body == "Body"


def test_parse_block_array():
    raw = (
        "---\n"
        "tags:\n"
        "  - alpha\n"
        "  - beta\n"
        "  - gamma\n"
        "  - delta\n"
        "---\n"
        "Body"
    )
    props, _body, has_fm = parse_frontmatter(raw)
    assert has_fm is True
    assert props == {"tags": ["alpha", "beta", "gamma", "delta"]}


def test_parse_quoted_string_strips_quotes():
    raw = '---\ntitle: "with: a colon"\n---\nBody'
    props, _body, _ = parse_frontmatter(raw)
    assert props == {"title": "with: a colon"}


def test_parse_unclosed_fence_falls_through():
    raw = "---\nkind: shape\nNo closing fence here."
    props, body, has_fm = parse_frontmatter(raw)
    assert has_fm is False
    assert props == {}
    assert body == raw


def test_serialize_round_trip_scalars():
    props = {
        "kind": "shape",
        "topic": "n8n",
        "replay_count": 12,
        "is_archived": False,
    }
    body = "Some content"
    rendered = serialize_frontmatter(props, body)
    re_props, re_body, has_fm = parse_frontmatter(rendered)
    assert has_fm is True
    assert re_props == props
    assert re_body == body


def test_serialize_round_trip_with_array_short_uses_flow():
    props = {"tags": ["lesson", "auto", "kind/shape"]}
    rendered = serialize_frontmatter(props, "")
    assert "tags: [lesson, auto, kind/shape]" in rendered
    re_props, _, _ = parse_frontmatter(rendered)
    assert re_props == props


def test_serialize_long_array_uses_block_form():
    props = {"tags": ["a", "b", "c", "d", "e"]}
    rendered = serialize_frontmatter(props, "")
    assert "tags:\n  - a\n  - b\n" in rendered
    re_props, _, _ = parse_frontmatter(rendered)
    assert re_props == props


def test_serialize_quotes_yaml_significant_chars():
    props = {"title": "Skill shape · n8n/create: foo"}
    rendered = serialize_frontmatter(props, "body")
    # Should be quoted because of the colon
    assert '"Skill shape · n8n/create: foo"' in rendered
    re_props, _, _ = parse_frontmatter(rendered)
    assert re_props == props


def test_serialize_empty_props_returns_body_unchanged():
    body = "No frontmatter wanted."
    assert serialize_frontmatter({}, body) == body


def test_merge_updates_existing():
    original = (
        "---\n"
        "kind: shape\n"
        "replay_count: 3\n"
        "outcome: pending\n"
        "---\n"
        "Run log\n- 2026-04-29 success"
    )
    merged = merge_frontmatter(
        original, {"replay_count": 4, "outcome": "verified"}
    )
    props, body, _ = parse_frontmatter(merged)
    assert props["kind"] == "shape"
    assert props["replay_count"] == 4
    assert props["outcome"] == "verified"
    assert body.startswith("Run log")


def test_merge_with_none_deletes_key():
    original = (
        "---\nkind: shape\npending_since_turn: 12\noutcome: pending\n---\nBody"
    )
    merged = merge_frontmatter(
        original, {"pending_since_turn": None, "outcome": "verified"}
    )
    props, _, _ = parse_frontmatter(merged)
    assert "pending_since_turn" not in props
    assert props["outcome"] == "verified"


def test_merge_on_content_without_frontmatter_creates_one():
    body = "Plain body, no FM."
    merged = merge_frontmatter(body, {"kind": "shape", "replay_count": 1})
    props, out_body, has_fm = parse_frontmatter(merged)
    assert has_fm is True
    assert props == {"kind": "shape", "replay_count": 1}
    assert out_body == body


def test_serialize_float_whole_number_drops_trailing_zero():
    rendered = serialize_frontmatter({"v": 7.0}, "")
    # 7.0 → "7" (clean integer rendering)
    assert "v: 7\n" in rendered


@pytest.mark.parametrize(
    "value",
    [
        "plain string",
        "with spaces and/slashes",
        "topic/n8n",
        "intent/google-sheet-create",
    ],
)
def test_unquoted_strings_round_trip(value):
    props = {"key": value}
    rendered = serialize_frontmatter(props, "")
    re_props, _, _ = parse_frontmatter(rendered)
    assert re_props == props
