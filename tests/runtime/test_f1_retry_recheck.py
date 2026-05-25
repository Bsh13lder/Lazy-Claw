"""Tests for the F1 retry RE-CHECK gate fix (2026-05-23).

The bug being closed: ``agent.py`` ran ``detect_confabulation`` ONLY on
the first draft, gated by ``not _confab_injected``. Once the raw-data
injection retry fired, the retry's output was accepted blindly — even
if it still contained the same hallucination pattern. Today's failure:
"08:39 UTC since last check" + orphan-bubble fabrication survived the
retry and shipped to the user because the post-retry check never ran.

These tests pin the new contract: every draft on a channel-read turn
goes through both ``detect_confabulation`` and
``phase2_enforcement_verdict``, with retries capped at
``_F1_CONFAB_MAX_RETRIES`` before degrading to a logged accept.

We don't spin up the full ``process_message`` stack — that would need
a live LLM router, MCP servers, encrypted DB, etc. Instead we exercise
the helpers in isolation and assert the constants + the new module
contracts that the agent.py call-site depends on.
"""

from __future__ import annotations

import re

import pytest

# Importing agent.py would drag in the full LLM router (and the host
# may not have all provider SDKs). Read the source as text for the
# structural assertions — much faster + zero-cost.
_AGENT_PY = "/Users/blckit/Desktop/Code_Projects/lazyclaw/lazyclaw/runtime/agent.py"


def _agent_source() -> str:
    with open(_AGENT_PY, encoding="utf-8") as fp:
        return fp.read()


# ─── Module-level constants ──────────────────────────────────────────────


def test_confab_max_retries_constant_defined() -> None:
    """The new retry cap must exist as a module constant."""
    src = _agent_source()
    assert re.search(r"_F1_CONFAB_MAX_RETRIES:\s*int\s*=\s*2", src), (
        "_F1_CONFAB_MAX_RETRIES = 2 must be declared at module scope"
    )


def test_confab_retries_counter_initialized() -> None:
    """The per-turn counter must be initialized inside process_message."""
    src = _agent_source()
    assert re.search(r"_confab_retries:\s*int\s*=\s*0", src), (
        "_confab_retries counter must be initialized to 0 per turn"
    )


# ─── Retry gate semantics ────────────────────────────────────────────────


def test_old_blind_gate_removed() -> None:
    """The ``not _confab_injected`` gate that skipped post-retry checks
    must be removed from the confabulation backstop block.

    A regression here re-introduces the bug where the retry's output
    ships without being re-checked against the detector.
    """
    src = _agent_source()
    # Find the confabulation backstop block (between the F1 phase-1 block
    # and the knowledge-gap self-recall block).
    start = src.find("# F1 confabulation backstop")
    assert start != -1, "F1 confab backstop block not found"
    end = src.find("# Knowledge-gap self-recall", start)
    assert end != -1, "Knowledge-gap self-recall block not found"
    block = src[start:end]
    # Strip comment lines so we only check ACTIVE code — the comment
    # explaining the historical removal mentions the phrase by design.
    code_only = "\n".join(
        line for line in block.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "not _confab_injected" not in code_only, (
        "The `not _confab_injected` gate is back in ACTIVE CODE — "
        "retries will be accepted blindly again. See 2026-05-23 fix."
    )


def test_retry_block_runs_detector_unconditionally_on_channel_turn() -> None:
    """The gate condition must only require channel-read + content."""
    src = _agent_source()
    start = src.find("# F1 confabulation backstop")
    end = src.find("# Knowledge-gap self-recall", start)
    block = src[start:end]
    # The opening `if (_final_content and any(_is_channel_read_tool…))`
    # must be present without the old `_confab_injected` term.
    assert "_is_channel_read_tool" in block
    assert "_final_content" in block


def test_phase2_enforcement_wired_in_with_safe_fallback() -> None:
    """phase2_enforcement_verdict must be imported with an ImportError
    fallback so a stale container without the helper still boots."""
    src = _agent_source()
    start = src.find("# F1 confabulation backstop")
    end = src.find("# Knowledge-gap self-recall", start)
    block = src[start:end]
    assert "phase2_enforcement_verdict" in block, (
        "Phase-2 helper must be called inside the F1 backstop block"
    )
    assert "ImportError" in block, (
        "Phase-2 import must have an ImportError fallback so a stale "
        "container without the helper still boots"
    )


def test_confab_retries_counter_bumped_on_inject() -> None:
    """The counter must be incremented inside the injection branch."""
    src = _agent_source()
    start = src.find("# F1 confabulation backstop")
    end = src.find("# Knowledge-gap self-recall", start)
    block = src[start:end]
    assert "_confab_retries += 1" in block, (
        "Counter must be incremented per retry attempt"
    )


def test_degraded_accept_log_marker_present() -> None:
    """When retries exhaust, the loud `[F1-accepted-degraded]` log marker
    must fire so we can grep production for these events."""
    src = _agent_source()
    start = src.find("# F1 confabulation backstop")
    end = src.find("# Knowledge-gap self-recall", start)
    block = src[start:end]
    assert "[F1-accepted-degraded]" in block


def test_post_retry_rewrite_forced_log_marker_present() -> None:
    """When the SECOND retry fires (first injection didn't clear), a
    distinct log marker fires so we can measure how often the second
    retry is actually load-bearing."""
    src = _agent_source()
    start = src.find("# F1 confabulation backstop")
    end = src.find("# Knowledge-gap self-recall", start)
    block = src[start:end]
    assert "[F1-post-retry-rewrite-forced]" in block


# ─── End-to-end behavioral test of phase2_enforcement_verdict ────────────


def test_phase2_helper_blocks_unverified_channel_read_quote() -> None:
    """Smoke test that the helper the retry gate now calls actually
    fires on the failure pattern we shipped today."""
    from lazyclaw.runtime.f1_content_verifier import (
        phase2_enforcement_verdict,
    )
    # Simulate today's failure: brain emitted a quote that's not in
    # the live tool data.
    reply = (
        "> James Blue (9:12 PM): I want the moon delivered tomorrow.\n"
    )
    tool_call_history = ["upwork_get_conversation"]
    tool_results = [
        '{"messages":[{"sender":"James","content":"Different content."}]}'
    ]
    should_block, reason = phase2_enforcement_verdict(
        reply, tool_call_history, tool_results,
    )
    assert should_block is True
    assert "unverified" in reason


def test_phase2_helper_passes_clean_quote() -> None:
    from lazyclaw.runtime.f1_content_verifier import (
        phase2_enforcement_verdict,
    )
    quoted = "I want the moon delivered tomorrow."
    reply = f"> James Blue (9:12 PM): {quoted}\n"
    tool_call_history = ["upwork_get_conversation"]
    tool_results = [f'{{"messages":[{{"sender":"James","content":"{quoted}"}}]}}']
    should_block, _ = phase2_enforcement_verdict(
        reply, tool_call_history, tool_results,
    )
    assert should_block is False
