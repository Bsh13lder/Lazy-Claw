"""History read-time stripping of internal reasoning blocks.

Assistant rows persist their <plan>/<taor_plan>/<think> preamble; no
client renders XML, so users saw raw plan markup above every reply
(2026-08-14). The route strips at read time — stored rows unchanged.
"""

from lazyclaw.gateway.routes.chat_history import _strip_internal_blocks


def test_closed_plan_block_removed():
    s = "<plan>\n<goal>Do X</goal>\n</plan>\nReal answer here."
    assert _strip_internal_blocks(s) == "Real answer here."


def test_taor_plan_and_think_removed():
    s = "<taor_plan>steps</taor_plan><think>hmm</think>Answer."
    assert _strip_internal_blocks(s) == "Answer."


def test_dangling_open_tag_scrubbed():
    s = "<plan>\n<goal>cut mid-stream"
    out = _strip_internal_blocks(s)
    assert "<plan>" not in out


def test_clean_text_untouched():
    s = "Just a normal reply with `code` and a < sign."
    assert _strip_internal_blocks(s) == s


def test_plan_between_text_segments():
    s = "Intro.<plan>x</plan>Outro."
    assert _strip_internal_blocks(s) == "Intro.Outro."
