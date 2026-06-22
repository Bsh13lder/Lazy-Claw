"""Tests for the brain-failure handling fixes (2026-06-22).

Bug 2 — raw-error leak: ``agent.py`` pasted ``f"Sorry, an error occurred:
{exc}"`` into the user-facing reply, so a caught brain exception surfaced as
``Sorry, an error occurred: Claude SDK iterator error: Claude Code returned an
error result: success`` on Telegram.

Bug 3 — failure spiral: that canned response (content set, tool_calls=[]) was
treated as a real reply, hit the channel-tool nudge, and re-looped — observed
as ~12 failed brain calls in 70s for one message.

Following the codebase convention (see test_f1_retry_recheck.py): we do NOT
spin up the full process_message stack (it needs a live LLM router, MCP, and
an encrypted DB). We pin the fix contract with source-structural assertions on
agent.py plus a behavioural check of the render_error helper.
"""
from __future__ import annotations

import re

import pytest

from lazyclaw.channels.telegram_view import render_error

_AGENT_PY = (
    "/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/agent.py"
)


def _agent_source() -> str:
    with open(_AGENT_PY, encoding="utf-8") as fp:
        return fp.read()


def _code_only(src: str) -> str:
    """Strip comment lines so assertions only see ACTIVE code."""
    return "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )


# ─── Bug 2: no raw exception text leaks to the user ──────────────────────

def test_no_raw_exception_leak_string_remains():
    code = _code_only(_agent_source())
    assert 'Sorry, an error occurred' not in code, (
        "The raw-exception leak string is back — caught brain failures will "
        "paste internals into the user's chat again (Bug 2)."
    )


def test_render_error_used_for_brain_failures():
    code = _code_only(_agent_source())
    # Both the chat path and the streaming path must route through the card.
    assert code.count('_render_error("brain")') >= 2, (
        "Both brain-failure paths (chat + stream) must use render_error('brain')"
    )
    assert code.count('_render_error("rate_limit")') >= 2, (
        "Both paths must keep a rate-limit card"
    )


@pytest.mark.parametrize("kind", ["brain", "rate_limit", "generic"])
def test_error_card_leaks_nothing(kind):
    out = render_error(kind)
    for tok in ["Traceback", "Exception", "SDK", "iterator",
                "Claude Code returned", "error result: success"]:
        assert tok not in out
    assert out.strip()


# ─── Bug 3: caught brain failure terminates the turn (no spiral) ─────────

def test_brain_errored_flag_initialized():
    code = _code_only(_agent_source())
    assert re.search(r"_brain_errored\s*=\s*False", code), (
        "_brain_errored must be initialized to False per turn"
    )


def test_both_failure_paths_set_the_flag():
    code = _code_only(_agent_source())
    assert code.count("_brain_errored = True") >= 2, (
        "Both the chat and streaming except blocks must set _brain_errored=True"
    )


def test_guard_appends_and_breaks_not_early_return():
    """The guard must append the error card to all_new_messages and BREAK —
    NOT early-return. An early return skips the post-loop agent_messages
    persistence, crashes in the finally on an unbound `content`, and leaks the
    foreground TeamLead task (regression caught 2026-06-22 adversarial review).
    """
    src = _agent_source()
    anchor = src.find("if _brain_errored:")
    assert anchor != -1, "Missing the `if _brain_errored:` guard"
    block = src[anchor:anchor + 1400]
    assert "all_new_messages.append(" in block, (
        "The guard must append the error card to all_new_messages so the "
        "post-loop persist block writes the turn to agent_messages."
    )
    # The guard must break out of the loop (terminating the spiral) and must
    # NOT early-return out of the function (which skipped persistence + finally).
    assert "\n                    break" in block, (
        "The _brain_errored guard must `break` to flow through the post-loop "
        "persist + teardown."
    )
    assert "return response.content" not in block, (
        "The guard must NOT early-return — that skips persistence, crashes in "
        "the finally on unbound `content`, and leaks the foreground task."
    )


def test_guard_precedes_the_channel_tool_nudge():
    """The early-return must come BEFORE the 'Nudging tool use' re-loop so a
    failed brain turn can never reach the nudge `continue`."""
    src = _agent_source()
    guard = src.find("if _brain_errored:")
    nudge = src.find("Nudging tool use: LLM skipped tools for channel query")
    assert guard != -1 and nudge != -1
    assert guard < nudge, (
        "The _brain_errored guard must precede the channel-tool nudge — "
        "otherwise the error response still triggers a re-loop (Bug 3)."
    )
