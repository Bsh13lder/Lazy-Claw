"""Tests for the block-aware Docs AI edit plan (Task A3)."""

from lazyclaw.docs import ai_edit


def test_normalize_accepts_block_dicts():
    blocks = ai_edit._normalize_blocks(
        [
            {"type": "heading", "level": 2, "text": "Steps"},
            {"type": "number", "text": "first"},
            {"type": "number", "text": "second"},
        ]
    )
    assert blocks[0]["type"] == "heading" and blocks[0]["level"] == 2
    assert [b["type"] for b in blocks[1:]] == ["number", "number"]
    assert blocks[1]["runs"] == [{"text": "first"}]


def test_normalize_markdown_string_becomes_blocks():
    blocks = ai_edit._normalize_blocks(["# Title", "- a", "- b"])
    assert blocks[0]["type"] == "heading"
    assert blocks[1]["type"] == "bullet" and blocks[2]["type"] == "bullet"


def test_normalize_legacy_runs_dict():
    blocks = ai_edit._normalize_blocks(
        [{"runs": [{"text": "x", "url": "https://x.io"}]}]
    )
    assert blocks[0]["type"] == "paragraph"
    assert blocks[0]["runs"][0]["url"] == "https://x.io"


def test_plan_shape_mentions_lists():
    assert "number" in ai_edit.PLAN_SHAPE or "bullet" in ai_edit.PLAN_SHAPE


def test_system_prompt_forbids_literal_list_markers():
    assert "1." in ai_edit._SYSTEM or "literal" in ai_edit._SYSTEM.lower()
