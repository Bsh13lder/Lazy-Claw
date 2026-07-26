"""The safety net that never fired during the 2026-07-26 Upwork loop.

The specialist rotated three tools for ELEVEN MINUTES:

    mcp_<uuid>_upwork_get_messages   (257 chars, byte-identical each time)
    upwork_last_conversation         (92 chars,  byte-identical each time)
    browser                          (~13 000 chars, varying)

Both existing detectors were structurally blind to it:

  * ``detect_tool_loop`` bails on ``if len(set(last_n)) != 1`` — it needs
    the SAME tool name N times consecutively. Names alternated, so the
    counter never got past 1.
  * ``detect_same_result`` compares consecutive results, which differed by
    two orders of magnitude (257 / 92 / 13000) — far under the 0.85
    similarity floor.

Separately, every bridged MCP tool is named ``mcp_<server_uuid>_<tool>``.
No entry in ``DEFAULT_LOOP_LIMITS`` and no prefix in
``_BATCH_OP_PREFIXES`` can match that, so every MCP tool silently falls
to ``"default": 3`` under a name no rule can address. Same shape as the
``registry.get("whatsapp_send") is always None`` incident.

A cycle detector that fires on a CORRECT read path is worse than the bug
it fixes, so the negative cases below are the load-bearing ones.
"""

from __future__ import annotations

from lazyclaw.runtime.stuck_detector import (
    DEFAULT_LOOP_LIMITS,
    _effective_limit,
    detect_cycle,
    detect_stuck,
)

UUID_PREFIX = "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_"

# Byte-identical bodies, exactly as in the incident.
GET_MESSAGES_RESULT = '{"room_id": "room_e09f", "contact_name": ""}'
LAST_CONV_RESULT = (
    "No Upwork conversations found. Make sure you're logged in and "
    "have at least one chat thread."
)


def _incident_trace(repeats: int = 3):
    """The real rotation, repeated `repeats` times."""
    history: list[str] = []
    results: list[str] = []
    for i in range(repeats):
        history += [
            f"{UUID_PREFIX}upwork_get_messages",
            "upwork_last_conversation",
            "browser",
        ]
        results += [
            GET_MESSAGES_RESULT,
            LAST_CONV_RESULT,
            f"<accessibility snapshot {i} " + "x" * 500 + ">",
        ]
    return history, results


class TestMcpUuidPrefixStripping:
    """Bridged MCP tools must resolve limits under their BARE name."""

    def test_uuid_prefixed_upwork_tool_gets_batch_limit(self):
        limit = _effective_limit(
            f"{UUID_PREFIX}upwork_get_messages", DEFAULT_LOOP_LIMITS,
        )
        assert limit == 10, (
            "bridged upwork tool should resolve to the batch-op limit, "
            f"got {limit}"
        )

    def test_uuid_prefixed_email_tool_gets_batch_limit(self):
        assert _effective_limit(
            f"{UUID_PREFIX}email_get_messages", DEFAULT_LOOP_LIMITS,
        ) == 10

    def test_bare_upwork_tool_gets_batch_limit(self):
        assert _effective_limit(
            "upwork_get_messages", DEFAULT_LOOP_LIMITS,
        ) == 10

    def test_uuid_prefixed_name_hits_explicit_limits_table(self):
        # A bridged tool whose bare name IS in the table must use it.
        assert _effective_limit(
            f"{UUID_PREFIX}browser", DEFAULT_LOOP_LIMITS,
        ) == DEFAULT_LOOP_LIMITS["browser"]

    def test_non_mcp_name_unchanged(self):
        assert _effective_limit("browser", DEFAULT_LOOP_LIMITS) == 5
        assert _effective_limit("some_random_tool", DEFAULT_LOOP_LIMITS) == 3

    def test_malformed_mcp_name_is_not_mangled(self):
        # `mcp_` but no UUID — must NOT be blindly split on underscores.
        assert _effective_limit(
            "mcp_not_a_uuid_email_thing", DEFAULT_LOOP_LIMITS,
        ) == 3

    def test_mcp_prefix_alone_is_safe(self):
        assert _effective_limit("mcp_", DEFAULT_LOOP_LIMITS) == 3


class TestCycleDetectorFiresOnTheIncident:
    def test_real_incident_trace_trips_the_detector(self):
        history, results = _incident_trace(repeats=3)
        signal = detect_cycle(history, results)
        assert signal is not None, "the 11-minute rotation must be caught"
        assert signal.reason == "cycle"

    def test_two_element_cycle_trips(self):
        history = ["a_tool", "b_tool"] * 3
        results = ["same-a", "same-b"] * 3
        assert detect_cycle(history, results) is not None

    def test_detect_stuck_surfaces_the_cycle(self):
        history, results = _incident_trace(repeats=3)
        signal = detect_stuck(history, results, last_result=results[-1])
        assert signal is not None
        assert signal.reason == "cycle"


class TestCycleDetectorDoesNotFireOnHealthyWork:
    """These are the tests that matter. A false positive here would
    break correct read paths in production."""

    def test_batch_read_with_varying_results_does_not_trip(self):
        # search_notes -> get_note(a) -> search_notes -> get_note(b) ...
        history = ["search_notes", "get_note"] * 3
        results = [
            "hits: note_1, note_2", "note_1 body: alpha",
            "hits: note_3, note_4", "note_3 body: beta",
            "hits: note_5, note_6", "note_5 body: gamma",
        ]
        assert detect_cycle(history, results) is None

    def test_browser_workflow_does_not_trip(self):
        history = ["browser", "screenshot"] * 3
        results = [
            "clicked #login", "<png 1>",
            "clicked #submit", "<png 2>",
            "clicked #next", "<png 3>",
        ]
        assert detect_cycle(history, results) is None

    def test_new_tool_name_in_window_suppresses(self):
        # Progress is being made — a genuinely new tool appeared.
        history = [
            "a_tool", "b_tool", "a_tool", "b_tool", "a_tool", "c_tool",
        ]
        results = ["x", "y", "x", "y", "x", "z"]
        assert detect_cycle(history, results) is None

    def test_short_history_does_not_trip(self):
        history = ["a_tool", "b_tool", "a_tool"]
        results = ["x", "y", "x"]
        assert detect_cycle(history, results) is None

    def test_single_tool_repetition_left_to_tool_loop(self):
        # detect_tool_loop owns this shape; the cycle detector must not
        # steal it (different remediation message).
        history = ["a_tool"] * 6
        results = ["same"] * 6
        assert detect_cycle(history, results) is None

    def test_empty_inputs_are_safe(self):
        assert detect_cycle([], []) is None
        assert detect_cycle(["a"], []) is None

    def test_mismatched_lengths_are_safe(self):
        history = ["a_tool", "b_tool"] * 3
        assert detect_cycle(history, ["only-one-result"]) is None

    def test_all_results_varying_does_not_trip(self):
        # Rotation, but every call returns something new = progress.
        history = ["a_tool", "b_tool", "c_tool"] * 2
        results = [f"distinct result number {i}" for i in range(6)]
        assert detect_cycle(history, results) is None
