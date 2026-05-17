"""AUTO-PROMOTE guard for meta/reflection questions.

The text-only AUTO-PROMOTE failsafe at agent.py:3660 force-submits the
user's original message to task_runner when the brain returns text
instead of dispatching a tool. This is correct for actionable tasks
("scan upwork for new jobs") that the brain dodged, but WRONG for
meta/reflection questions ("why did you use code agent?", "what tools
do you have?") — those should stay conversational, not get force-
dispatched as bg tasks that then re-trigger claude-code MCP in a loop.

Observed 2026-05-16 / 2026-05-17:
- User typed "why u usde code ahent ??? it has not that tools just find
  tools ad you ccan do that"
- Brain went text-only at iter=2 (had been narrowed to run_background)
- AUTO-PROMOTE failsafe fired → submitted to task_runner
- Bg agent re-invoked claude-code MCP on the meta question (loop)
- User complained: "why was code specialist executed ???"

Fix: ``_is_meta_question(text)`` heuristic — starts with a wh-/how
word AND contains '?'. Returns True for the guard to skip the
auto-promote and let the brain's text reply surface.
"""

from __future__ import annotations

import pytest

from lazyclaw.runtime.agent import _is_meta_question


class TestMetaQuestionDetection:
    """``_is_meta_question`` is conservative: only fires on clear
    meta/reflection patterns (wh-word leader + '?'). Plain commands
    that happen to contain 'why' / 'what' inline DO NOT count.
    """

    # ── Positive cases: should be detected as meta ────────────────

    @pytest.mark.parametrize("text", [
        "why u usde code ahent ??? it has not that tools just find tools",
        "why did you use the code agent?",
        "what tools do you have?",
        "what can you do?",
        "how come you ran claude-code?",
        "how did that happen?",
        "when did you start that task?",
        "where is the workspace?",
        "who told you to do that?",
        # leading whitespace + capitalization variants
        "  Why are you doing this?",
        "WHY DID YOU DO THAT???",
        "What about the upwork inbox?",
    ])
    def test_meta_questions_detected(self, text: str) -> None:
        assert _is_meta_question(text) is True, (
            f"Should detect as meta: {text!r}"
        )

    # ── Negative cases: NOT meta — real tasks / commands ──────────

    @pytest.mark.parametrize("text", [
        # Actionable tasks without '?' — must auto-promote
        "scan upwork for new contracts",
        "find me python freelance jobs",
        "send a message to james blue",
        "draft a reply to the last upwork thread",
        # Tasks that mention wh-words inline but aren't questions
        "I want to know what makes a good upwork proposal — apply to the next 3",
        "search for tools that handle PDF extraction and apply them",
        # Imperative even though it ends with '?'
        "can you scan upwork for me?",  # 'can' is not in the wh-list
        "could you find me 3 python jobs?",
        # Empty / whitespace-only
        "",
        "   ",
        # Questions without '?' — ambiguous, treat as actionable
        "why dont you scan upwork",
    ])
    def test_actionable_messages_not_meta(self, text: str) -> None:
        assert _is_meta_question(text) is False, (
            f"Should NOT detect as meta: {text!r}"
        )

    def test_none_input_safe(self) -> None:
        """Defensive: ``None`` or non-string input must not crash."""
        # Don't require any specific behaviour — just that it returns
        # a bool, doesn't raise, and treats no-text as not-meta.
        assert _is_meta_question(None) is False  # type: ignore[arg-type]
