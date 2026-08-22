"""Integration tests for the NL ask-conversation hook wired into agent.py.

The hook lives in ``_process_message_inner`` and is tested here via source
inspection (the established pattern across this test suite — no test in
``tests/runtime/`` instantiates a live ``Agent``, because constructing one
requires a live ``Config``, ``LLMRouter``, ``EcoRouter``, DB connection, and
MCP servers).

Three scenarios are pinned:

(a) Hook fires for a matching message: emits ``INSTANT_COMMAND``, returns
    the string from ``start_ask_conversation``, never reaches the brain.
(b) Non-matching message: the hook block is skipped entirely.
(c) Exception safety: the try/except wraps the whole hook so a crash inside
    ``match_ask_conversation`` or ``start_ask_conversation`` falls through
    to the brain path without propagating.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.runtime.instant_dispatch import match_ask_conversation, _ASK_WHO_DENYLIST

_AGENT_SRC = (
    Path(__file__).parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
).read_text()


# ---------------------------------------------------------------------------
# (a) Hook fires on match: structural verification
# ---------------------------------------------------------------------------

def test_hook_imports_both_helpers() -> None:
    """Both match_ask_conversation and start_ask_conversation must be imported
    inside the hook block (lazy import pattern mirroring instant_dispatch)."""
    assert "match_ask_conversation" in _AGENT_SRC
    assert "start_ask_conversation" in _AGENT_SRC


def test_hook_checks_match_is_not_none() -> None:
    """Hook must guard on _ask_match is not None before awaiting the runner."""
    # The conditional that gates the runner call
    assert "_ask_match is not None" in _AGENT_SRC


def test_hook_emits_instant_command_event() -> None:
    """On a match, hook must emit an INSTANT_COMMAND event (same as the
    instant-dispatch route above it)."""
    # Locate the ask_conversation hook block via its path tag
    idx = _AGENT_SRC.index('"path": "ask_conversation"')
    # Walk backwards to verify INSTANT_COMMAND is in the same block
    nearby = _AGENT_SRC[max(0, idx - 400):idx + 200]
    assert "INSTANT_COMMAND" in nearby


def test_hook_returns_ask_reply() -> None:
    """Hook must return _ask_reply so the brain never runs on a match."""
    idx = _AGENT_SRC.index('"path": "ask_conversation"')
    nearby = _AGENT_SRC[idx:idx + 300]
    assert "return _ask_reply" in nearby


def test_hook_emits_done_event() -> None:
    """Hook must also emit a 'done' event so the caller sees turn completion."""
    idx = _AGENT_SRC.index('"path": "ask_conversation"')
    # The done event follows right after INSTANT_COMMAND in the block
    nearby = _AGENT_SRC[idx:idx + 400]
    assert '"done"' in nearby


# ---------------------------------------------------------------------------
# (b) Non-matching message: hook block skipped
# ---------------------------------------------------------------------------

def test_hook_is_inside_try_except() -> None:
    """The ask hook must be wrapped in try/except so an exception (e.g. an
    unexpected import failure) falls through to the brain path — not crash."""
    idx = _AGENT_SRC.index('"path": "ask_conversation"')
    # The try: must appear before the hook block
    head = _AGENT_SRC.rindex("try:", 0, idx)
    # And the except: clause must follow it (before the plan-mode gate)
    except_idx = _AGENT_SRC.index("except Exception:", head)
    plan_gate_idx = _AGENT_SRC.index("# ── Plan-mode fix-task gate", head)
    assert except_idx < plan_gate_idx, (
        "except clause for ask_conversation hook must appear before plan gate"
    )


def test_hook_fallthrough_logged_not_raised() -> None:
    """On exception the hook must log.debug (not log.error / raise)."""
    idx = _AGENT_SRC.index("ask_conversation hook raised; continuing to brain path")
    nearby = _AGENT_SRC[max(0, idx - 60):idx + 100]
    assert "logger.debug" in nearby


# ---------------------------------------------------------------------------
# (c) Pronoun denylist exercised directly — verifies Fix 1 integrates cleanly
#     with the hook's entry point (match_ask_conversation is what the hook calls)
# ---------------------------------------------------------------------------

def test_match_returns_none_for_pronoun_me() -> None:
    """'ask me on whatsapp to remind John' must NOT match — 'me' is a pronoun."""
    assert match_ask_conversation("ask me on whatsapp to remind John") is None


def test_match_returns_none_for_pronoun_them() -> None:
    """'ask them via email about it' must NOT match — 'them' is a pronoun."""
    assert match_ask_conversation("ask them via email about it") is None


def test_real_name_still_matches() -> None:
    """Ensure the denylist doesn't clobber real contact names."""
    result = match_ask_conversation("ask Alice on whatsapp if she's coming")
    assert result is not None
    assert result["who"] == "Alice"
    assert result["channel"] == "whatsapp"


def test_denylist_covers_all_common_pronouns() -> None:
    """Denylist must include the full set of singular/plural personal pronouns
    that could slip through the regex as contact names."""
    for pronoun in ("me", "them", "us", "you", "it", "him", "her",
                    "someone", "anyone", "somebody", "anybody"):
        assert pronoun in _ASK_WHO_DENYLIST, f"missing pronoun: {pronoun!r}"


# ---------------------------------------------------------------------------
# (d) Hook position — ask_conversation comes AFTER instant_dispatch route,
#     BEFORE the plan-mode gate (order of pre-brain short-circuits)
# ---------------------------------------------------------------------------

def test_hook_position_after_instant_dispatch() -> None:
    """Ask-conversation hook must come AFTER the instant-dispatch block."""
    instant_dispatch_idx = _AGENT_SRC.index("# ── Instant dispatch — single-skill 1:1 intents")
    ask_hook_idx = _AGENT_SRC.index("# ── NL ask-conversation trigger")
    assert instant_dispatch_idx < ask_hook_idx


def test_hook_position_before_plan_gate() -> None:
    """Ask-conversation hook must come BEFORE the plan-mode gate."""
    ask_hook_idx = _AGENT_SRC.index("# ── NL ask-conversation trigger")
    plan_gate_idx = _AGENT_SRC.index("# ── Plan-mode fix-task gate")
    assert ask_hook_idx < plan_gate_idx
