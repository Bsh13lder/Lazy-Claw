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


# ── [SILENT] turn hiding ───────────────────────────────────────────────
# Suppress-everywhere includes chat display: a [SILENT] reply hides its
# whole batch-persisted turn (shared created_at) from the history page.


def test_silent_turn_timestamps_collected():
    fetched = [
        (("id1", "user", "enc", None, None, "2026-08-14 19:01:00"),
         "[JOB:James watch] check inbox"),
        (("id2", "assistant", "enc", None, None, "2026-08-14 19:01:00"),
         "[SILENT] No new messages from James."),
        (("id3", "assistant", "enc", None, None, "2026-08-14 19:05:00"),
         "James replied! > James (19:04): hello"),
    ]
    silent = {
        r[5] for r, content in fetched
        if r[1] == "assistant" and content.startswith("[SILENT]")
    }
    assert silent == {"2026-08-14 19:01:00"}
    kept = [r[0] for r, _ in fetched if r[5] not in silent]
    assert kept == ["id3"]
