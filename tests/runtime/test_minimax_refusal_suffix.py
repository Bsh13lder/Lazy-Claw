"""The MiniMax router suffix must forbid the "I can't access X, do it yourself"
refusal framing, and must describe the post-fix narrowing behaviour (reads no
longer narrow the toolset).

2026-06-23: M3 narrated self-service how-tos ("I don't have access to your
account, here's how YOU can do it") instead of delegating. The fix is
prompt-level (SOUL.md-before-runtime rule), in the MiniMax-scoped suffix.
"""
from lazyclaw.runtime.personality import minimax_tool_discipline_suffix


def test_suffix_forbids_self_service_refusal():
    s = minimax_tool_discipline_suffix()
    assert "NEVER REFUSE" in s
    # the exact refusal framings appear as FORBIDDEN examples
    assert "don't have access to" in s
    assert "Here's how YOU can" in s
    assert "NOT a blocker — it is a delegation" in s
    # the original delegate-over-narrate line survives the append
    assert "Calling delegate is ALWAYS better than narrating" in s


def test_suffix_matches_read_then_act_cap_behaviour():
    """Consistency with the ``_is_inline_mutation`` cap fix: reads no longer
    narrow the toolset, so the prompt must NOT claim they do."""
    s = minimax_tool_discipline_suffix()
    assert "do NOT narrow your tools" in s or "read THEN act" in s
    assert "After ONE read-only inspection your tools narrow" not in s
