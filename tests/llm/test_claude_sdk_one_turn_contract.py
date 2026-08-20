"""SDK transport must enforce the one-turn-per-chat contract (max_turns=1).

Incident 2026-08-16/17 (himap blog): without ``max_turns``, the SDK runs its
OWN internal agentic loop. lazyclaw's @tool wrappers never execute anything —
they return the sentinel "[lazyclaw runtime] tool call recorded…" — so within
one SDK turn the model saw a placeholder "result" for EVERY tool call, retried
5-9 browser calls per batch, then escalated in despair (ask_brain: "Browser
tool calls have returned nothing but the placeholder string for 9 consecutive
calls"). The runner executed the harvested calls fine afterwards, but the
model never saw those results inside its turn — tasks stalled into the 600s
background timeout while the page itself worked.

With max_turns=1 the SDK stops after the first assistant turn: the model
emits its tool batch, never sees a sentinel, and lazyclaw's runtime owns
execution — the contract the provider docstring always promised. A turn that
ends this way reports subtype="error_max_turns"; that is the EXPECTED outcome
when tool calls were harvested, not an error.
"""

from __future__ import annotations

import pytest

from lazyclaw.llm.providers.claude_sdk_provider import (
    ClaudeSDKProvider,
    _max_turns_tail_action,
    _result_error_text,
)


class TestBuildOptionsOneTurn:
    def test_max_turns_is_one(self):
        pytest.importorskip("claude_agent_sdk")
        provider = ClaudeSDKProvider(model="sonnet", claude_bin="/bin/true")

        def _fake_create_server(**_kw):  # never called with empty tools
            raise AssertionError("no tools → no MCP server")

        options, _ = provider._build_options(
            [], _fake_create_server, system_prompt=None, user_id="u1",
        )
        assert options.max_turns == 1


class TestResultErrorText:
    def test_no_error_returns_none(self):
        assert _result_error_text(
            is_error=False, subtype="success", errors=None,
            result=None, has_output=False,
        ) is None

    def test_max_turns_with_harvested_output_is_benign(self):
        # The by-design stop: model emitted tool calls, SDK hit the 1-turn
        # cap. lazyclaw executes the calls — this must NOT surface as error.
        assert _result_error_text(
            is_error=True, subtype="error_max_turns", errors=[],
            result=None, has_output=True,
        ) is None

    def test_max_turns_with_no_output_is_an_error(self):
        text = _result_error_text(
            is_error=True, subtype="error_max_turns", errors=[],
            result=None, has_output=False,
        )
        assert text is not None

    def test_real_upstream_error_is_preserved(self):
        text = _result_error_text(
            is_error=True, subtype="success",
            errors=["API Error: safeguards flagged"], result=None,
            has_output=True,
        )
        assert text == "API Error: safeguards flagged"

    def test_error_without_errors_list_falls_back_to_result(self):
        text = _result_error_text(
            is_error=True, subtype="success", errors=None,
            result="boom", has_output=False,
        )
        assert text == "boom"

    def test_error_with_nothing_falls_back_to_unknown(self):
        text = _result_error_text(
            is_error=True, subtype="success", errors=None,
            result=None, has_output=False,
        )
        assert text == "unknown"


class TestMaxTurnsTailAction:
    """SDK 0.1.81 does NOT deliver the max_turns stop as a ResultMessage —
    its internal reader raises a bare Exception on the message channel:
    ``Claude Code returned an error result: Reached maximum number of
    turns (1)`` (observed live 2026-08-17 17:46, every chat() failed).
    With output already harvested that IS the successful end of our
    one-turn contract and must be absorbed, not raised."""

    _ERR = (
        "Claude Code returned an error result: "
        "Reached maximum number of turns (1)"
    )

    def test_swallow_when_output_harvested(self):
        assert _max_turns_tail_action(
            self._ERR, have_usable_response=True,
        ) == "swallow"

    def test_empty_turn_retries_once(self):
        """2026-08-20 19:23: a worker's turn hit max_turns=1 with NOTHING
        harvested (the SDK-side model burned its single turn on a
        built-in tool) and the specialist was declared dead after 3 min
        of good work — while the very next identical call succeeded. The
        zero-output stop is transient: retry ONCE before raising
        (mirrors _success_tail_action's empty-turn retry)."""
        assert _max_turns_tail_action(
            self._ERR, have_usable_response=False,
        ) == "retry"

    def test_empty_turn_raises_after_the_one_retry(self):
        assert _max_turns_tail_action(
            self._ERR, have_usable_response=False, already_retried=True,
        ) == "raise"

    def test_usable_output_swallows_even_after_retry(self):
        assert _max_turns_tail_action(
            self._ERR, have_usable_response=True, already_retried=True,
        ) == "swallow"

    def test_not_this_quirk_passes_through(self):
        assert _max_turns_tail_action(
            "Claude Code returned an error result: success",
            have_usable_response=True,
        ) is None
        assert _max_turns_tail_action(
            "some genuine iterator explosion",
            have_usable_response=True,
        ) is None
