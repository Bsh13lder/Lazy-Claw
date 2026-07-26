"""The specialist F1 gate must require quote PRESENCE, not just accuracy.

2026-07-26: a `freelance_specialist` turn read the Upwork thread, then
emitted 2411 chars of prose with ZERO quote lines, and the gate passed it::

    [f1] detect_confabulation start: reply_len=2411 tool_calls=5 tool_results=5
    [f1] parse_quote_lines: found=0
    [f1] verdict=clean
    [f1] evaluate_grounding: phase2_block=False

Cause: ``phase2_enforcement_verdict`` bails at ``if not quotes: return
False, ""`` — it verifies quote ACCURACY but never quote PRESENCE. So a
reply with one slightly-wrong quote is blocked while a reply with no
quotes at all ships. That is backwards: zero quotes is the worse failure.

The brain path has always enforced this via
``agent._check_f1_violation`` ("substantive reply without a `> sender
(ts): …` quote block"), complete with the carve-outs earned by the
2026-06-24 failed-read loop. The specialist gate simply was never wired
to it. These tests pin the wiring AND the carve-outs — a gate that fires
on a failed read or a short ack would re-open that loop.
"""

from __future__ import annotations

from lazyclaw.runtime.f1_gate import evaluate_grounding

READ_TOOL = "upwork_last_conversation"
GOOD_RESULT = "> James Blue (7:08 PM): I have to run out to work"
ERROR_RESULT = (
    "Error in upwork_last_conversation: [AUTHENTICATIONFAILED] "
    "Invalid credentials"
)

# Well over the substantive threshold, no quote line — the incident shape.
PROSE_NO_QUOTES = (
    "I reviewed the conversation with the client. They are looking for "
    "an automation build and mentioned a budget. The timeline seems "
    "flexible but they want progress soon. " + "x" * 400
)
WITH_QUOTES = (
    "> James Blue (7:08 PM): I have to run out to work\n\n"
    "- He is stepping away; no scope change.\n"
    "Next: wait for his follow-up."
)


class TestQuotePresenceIsEnforced:
    def test_incident_shape_is_blocked(self):
        """Substantive prose + real read + zero quotes => blocked."""
        v = evaluate_grounding(PROSE_NO_QUOTES, [READ_TOOL], [GOOD_RESULT])
        assert v.ok is False, "zero-quote reply after a real read must block"
        assert v.corrective_injection

    def test_reply_with_quote_block_passes(self):
        v = evaluate_grounding(WITH_QUOTES, [READ_TOOL], [GOOD_RESULT])
        assert v.ok is True

    def test_mixed_results_one_good_read_still_enforces(self):
        v = evaluate_grounding(
            PROSE_NO_QUOTES, [READ_TOOL], [ERROR_RESULT, GOOD_RESULT],
        )
        assert v.ok is False


class TestCarveOutsArePreserved:
    """Each of these firing would re-open the 2026-06-24 failed-read loop."""

    def test_failed_read_is_exempt(self):
        """Nothing quotable came back — demanding a quote loops forever."""
        v = evaluate_grounding(PROSE_NO_QUOTES, [READ_TOOL], [ERROR_RESULT])
        assert v.ok is True

    def test_short_ack_is_exempt(self):
        v = evaluate_grounding("ok, done", [READ_TOOL], [GOOD_RESULT])
        assert v.ok is True

    def test_empty_reply_is_exempt(self):
        v = evaluate_grounding("", [READ_TOOL], [GOOD_RESULT])
        assert v.ok is True

    def test_non_channel_turn_is_exempt(self):
        """No channel read ran — observe-only, never blocked."""
        v = evaluate_grounding(PROSE_NO_QUOTES, ["list_tasks"], ["3 tasks"])
        assert v.ok is True

    def test_empty_tool_history_is_exempt(self):
        v = evaluate_grounding(PROSE_NO_QUOTES, [], [])
        assert v.ok is True


class TestGateNeverCrashes:
    """FAIL-OPEN is load-bearing: a crashing gate must not break a turn."""

    def test_none_reply_is_safe(self):
        assert evaluate_grounding(None, [READ_TOOL], [GOOD_RESULT]).ok is True

    def test_none_results_are_safe(self):
        v = evaluate_grounding(PROSE_NO_QUOTES, [READ_TOOL], None)
        assert v.ok is True

    def test_non_string_results_are_safe(self):
        v = evaluate_grounding(PROSE_NO_QUOTES, [READ_TOOL], [None, 42, {}])
        assert isinstance(v.ok, bool)
