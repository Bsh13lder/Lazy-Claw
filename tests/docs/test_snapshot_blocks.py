"""Tests for rich-block snapshot build/read (Task A2).

Shapes verified against web/node_modules/@univerjs/core (2026-06-08):
- heading: paragraphStyle.namedStyleType numeric (HEADING_1=4, H2=5, H3=6)
- list:    bullet.listType string ("BULLET_LIST" / "ORDER_LIST")
- runs:    textRuns[].ts.bl / .it / .ul={s:1}
"""

from lazyclaw.docs import snapshot as D


def _snap(blocks):
    return {"id": "doc-1", "documentStyle": {}, "body": D.build_body_with_blocks(blocks)}


def test_heading_block_sets_numeric_named_style():
    body = D.build_body_with_blocks(
        [{"type": "heading", "level": 1, "runs": [{"text": "Title"}]}]
    )
    assert body["paragraphs"][0]["paragraphStyle"]["namedStyleType"] == 4


def test_heading_levels_map_to_4_5_6():
    body = D.build_body_with_blocks(
        [
            {"type": "heading", "level": 1, "runs": [{"text": "a"}]},
            {"type": "heading", "level": 2, "runs": [{"text": "b"}]},
            {"type": "heading", "level": 3, "runs": [{"text": "c"}]},
        ]
    )
    got = [p["paragraphStyle"]["namedStyleType"] for p in body["paragraphs"]]
    assert got == [4, 5, 6]


def test_bullet_block_sets_bullet():
    body = D.build_body_with_blocks(
        [{"type": "bullet", "level": 0, "runs": [{"text": "x"}]}]
    )
    assert body["paragraphs"][0]["bullet"]["listType"] == "BULLET_LIST"
    assert body["paragraphs"][0]["bullet"]["nestingLevel"] == 0


def test_number_block_sets_order_list():
    body = D.build_body_with_blocks(
        [{"type": "number", "level": 0, "runs": [{"text": "x"}]}]
    )
    assert body["paragraphs"][0]["bullet"]["listType"] == "ORDER_LIST"


def test_consecutive_number_items_share_list_id():
    body = D.build_body_with_blocks(
        [
            {"type": "number", "level": 0, "runs": [{"text": "a"}]},
            {"type": "number", "level": 0, "runs": [{"text": "b"}]},
        ]
    )
    lid0 = body["paragraphs"][0]["bullet"]["listId"]
    lid1 = body["paragraphs"][1]["bullet"]["listId"]
    assert lid0 == lid1


def test_separate_list_runs_get_distinct_ids():
    body = D.build_body_with_blocks(
        [
            {"type": "number", "level": 0, "runs": [{"text": "a"}]},
            {"type": "paragraph", "level": 0, "runs": [{"text": "break"}]},
            {"type": "number", "level": 0, "runs": [{"text": "b"}]},
        ]
    )
    assert body["paragraphs"][0]["bullet"]["listId"] != body["paragraphs"][2]["bullet"]["listId"]


def test_bold_run_emits_textrun_style():
    body = D.build_body_with_blocks(
        [{"type": "paragraph", "level": 0,
          "runs": [{"text": "hi "}, {"text": "bold", "bold": True}]}]
    )
    assert any(tr.get("ts", {}).get("bl") == 1 for tr in body["textRuns"])


def test_underline_uses_text_decoration_shape():
    body = D.build_body_with_blocks(
        [{"type": "paragraph", "level": 0, "runs": [{"text": "u", "underline": True}]}]
    )
    assert body["textRuns"][0]["ts"]["ul"] == {"s": 1}


def test_get_blocks_roundtrips_type_and_text():
    snap = _snap([{"type": "number", "level": 0, "runs": [{"text": "first"}]}])
    out = D.get_blocks(snap)
    assert out[0]["type"] == "number"
    assert out[0]["runs"][0]["text"] == "first"


def test_get_blocks_roundtrips_bold():
    snap = _snap(
        [{"type": "paragraph", "level": 0,
          "runs": [{"text": "plain "}, {"text": "strong", "bold": True}]}]
    )
    out = D.get_blocks(snap)
    bold_runs = [r for r in out[0]["runs"] if r.get("bold")]
    assert bold_runs and bold_runs[0]["text"] == "strong"


def test_get_blocks_roundtrips_heading():
    snap = _snap([{"type": "heading", "level": 2, "runs": [{"text": "Sec"}]}])
    out = D.get_blocks(snap)
    assert out[0]["type"] == "heading" and out[0]["level"] == 2


def test_get_blocks_roundtrips_hyperlink():
    snap = _snap(
        [{"type": "paragraph", "level": 0,
          "runs": [{"text": "go ", }, {"text": "here", "url": "https://x.io"}]}]
    )
    out = D.get_blocks(snap)
    link_runs = [r for r in out[0]["runs"] if r.get("url")]
    assert link_runs and link_runs[0]["url"] == "https://x.io"
    assert link_runs[0]["text"] == "here"


def test_append_blocks_preserves_existing_then_adds():
    snap = _snap([{"type": "heading", "level": 1, "runs": [{"text": "Title"}]}])
    out = D.append_blocks(snap, [{"type": "bullet", "level": 0, "runs": [{"text": "x"}]}])
    blocks = D.get_blocks(out)
    assert blocks[0]["type"] == "heading"
    assert blocks[-1]["type"] == "bullet"


def test_append_blocks_replaces_blank_doc():
    blank = D.blank_document("Empty")
    out = D.append_blocks(blank, [{"type": "paragraph", "level": 0, "runs": [{"text": "hi"}]}])
    blocks = [b for b in D.get_blocks(out) if b["runs"] and b["runs"][0]["text"]]
    assert len(blocks) == 1 and blocks[0]["runs"][0]["text"] == "hi"


def test_build_body_with_runs_still_works():
    body = D.build_body_with_runs([[{"text": "plain"}]])
    assert body["dataStream"].startswith("plain")
    assert body["textRuns"] == []
