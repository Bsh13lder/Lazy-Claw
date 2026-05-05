"""Force-dispatch + 3-strikes graceful-handoff guards in agent.py.

These tests pin three pieces of the 2026-05-04 fix into place so a future
refactor that drops them will fail this suite instead of silently
regressing into the original "AGENT IGNORES AUTO-PROMOTE" + "fabricates
uvx upwork-mcp --login" flail:

1. AUTO-PROMOTE flag (``_force_dispatch_only``) and ``_promote_iter``
   are initialised once per turn.
2. Tool-list narrowing inside the iter loop kicks in when the flag is set
   (brain physically can't pick anything but ``run_background``).
3. Failsafe ``task_runner.submit`` path fires when the brain still
   refuses to call ``run_background`` after AUTO-PROMOTE.
4. Per-tool consecutive-failure counter is wired into the result-handling
   block and a deterministic graceful handoff returns from the loop at
   3 strikes (no fabricated CLI command in the user-visible message).
"""

from __future__ import annotations

from pathlib import Path

import pytest


_AGENT_SRC = (
    Path(__file__).parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
).read_text()


# ---------------------------------------------------------------------------
# 1. State variables introduced by Fix 2 + Fix 3
# ---------------------------------------------------------------------------

def test_force_dispatch_only_initialised() -> None:
    """The flag that enables tool-list narrowing must exist at turn start."""
    assert "_force_dispatch_only = False" in _AGENT_SRC
    assert "_promote_iter: int | None = None" in _AGENT_SRC


def test_three_strikes_state_initialised() -> None:
    """Per-tool counter + break sentinel allocated once per turn."""
    assert "_tool_failure_count: dict[str, int] = {}" in _AGENT_SRC
    assert "_three_strikes_break: tuple[str, str] | None = None" in _AGENT_SRC


# ---------------------------------------------------------------------------
# 2. AUTO-PROMOTE sets the flag (was a soft message before — now hard)
# ---------------------------------------------------------------------------

def test_auto_promote_sets_force_dispatch_flag() -> None:
    """When AUTO-PROMOTE injects, the next iter must be locked to bg-only."""
    # Look for the pair near the existing _promoted_to_bg = True line.
    idx = _AGENT_SRC.index("_promoted_to_bg = True")
    nearby = _AGENT_SRC[idx : idx + 400]
    assert "_force_dispatch_only = True" in nearby, (
        "AUTO-PROMOTE branch must set _force_dispatch_only = True"
    )
    assert "_promote_iter = iteration" in nearby, (
        "AUTO-PROMOTE branch must capture iteration into _promote_iter"
    )


# ---------------------------------------------------------------------------
# 3. Tool list narrowing actually executes inside the iter loop
# ---------------------------------------------------------------------------

def test_tool_list_narrowed_to_run_background_when_forced() -> None:
    """Narrowing block must filter to run_background and update suppression."""
    assert "AUTO-PROMOTE enforced" in _AGENT_SRC
    # The list-comprehension that does the narrowing.
    narrow_signature = (
        't.get(\"function\", {}).get(\"name\") == \"run_background\"'
    )
    assert narrow_signature in _AGENT_SRC, (
        "Expected narrowing list-comp keying on function name"
    )
    # And the late-inject suppression set must be extended.
    assert "_suppressed_tool_names |= _other_names" in _AGENT_SRC


# ---------------------------------------------------------------------------
# 4. Failsafe runtime auto-submit when brain refuses run_background
# ---------------------------------------------------------------------------

def test_failsafe_auto_submits_to_task_runner() -> None:
    """If brain still refuses run_background after one extra iter, runtime
    submits the original message via task_runner — guarantees a chat-
    responsive turn no matter how stubborn the model is."""
    assert "AUTO-PROMOTE failsafe" in _AGENT_SRC
    # Submission call signature
    assert "self._task_runner.submit(" in _AGENT_SRC
    # Returns the canonical short reply
    assert (
        'return "Continuing in background — will report back when done."'
        in _AGENT_SRC
    )


# ---------------------------------------------------------------------------
# 5. 3-strikes failure increment + reset
# ---------------------------------------------------------------------------

def test_three_strikes_increments_on_error_and_resets_on_success() -> None:
    """The counter must increment on _is_err_result and pop on success."""
    # Increment line
    assert (
        "_tool_failure_count[_short] = (\n                                "
        "_tool_failure_count.get(_short, 0) + 1"
    ) in _AGENT_SRC
    # Reset line
    assert "_tool_failure_count.pop(_short, None)" in _AGENT_SRC
    # Threshold check
    assert "_tool_failure_count[_short] >= 3" in _AGENT_SRC


def test_three_strikes_strips_mcp_uuid_prefix() -> None:
    """Counter key is stable across MCP subprocess restarts."""
    # The canonicalisation comment + actual stripping logic
    assert "Strip ``mcp_<uuid>_`` so tool name is stable" in _AGENT_SRC
    assert '_short.startswith("mcp_")' in _AGENT_SRC
    assert '_short.split("_", 2)' in _AGENT_SRC


# ---------------------------------------------------------------------------
# 6. Graceful handoff message — deterministic, no fake CLI advice
# ---------------------------------------------------------------------------

def test_graceful_handoff_no_fake_cli_command() -> None:
    """The user-facing handoff text must NOT advise the fake
    `uvx upwork-mcp --login` command — that's the lie this fix prevents.

    We strip Python comment lines before checking so the explanatory
    comment block (which references the fake string as historical
    context) doesn't false-positive.
    """
    handoff_start = _AGENT_SRC.index("# ── 3-strikes graceful handoff ──")
    handoff_end = _AGENT_SRC.index(
        "# ── Auto-promote-to-background nudge ──", handoff_start,
    )
    handoff_block = _AGENT_SRC[handoff_start:handoff_end]

    code_only = "\n".join(
        line for line in handoff_block.splitlines()
        if line.lstrip().startswith("#") is False
    )

    assert "uvx upwork-mcp" not in code_only, (
        "User-facing handoff must not parrot the fake CLI command"
    )
    assert "tried `{_failed_tool}` 3 times" in code_only
    assert "Reply `continue`" in code_only


def test_graceful_handoff_routes_known_services_to_reauth_url() -> None:
    """Domain inference picks the right re-auth URL for the failed tool."""
    handoff_start = _AGENT_SRC.index("# ── 3-strikes graceful handoff ──")
    handoff_end = _AGENT_SRC.index(
        "# ── Auto-promote-to-background nudge ──", handoff_start,
    )
    block = _AGENT_SRC[handoff_start:handoff_end]

    assert "upwork.com/nx/find-work" in block
    assert "instagram.com" in block
    assert "web.whatsapp.com" in block
    assert "mail.google.com" in block


def test_graceful_handoff_returns_immediately_no_more_iters() -> None:
    """The break path must `return _handoff` so no further iters fire."""
    handoff_start = _AGENT_SRC.index("# ── 3-strikes graceful handoff ──")
    handoff_end = _AGENT_SRC.index(
        "# ── Auto-promote-to-background nudge ──", handoff_start,
    )
    block = _AGENT_SRC[handoff_start:handoff_end]

    assert "return _handoff" in block, (
        "3-strikes block must return the handoff string, not just append"
    )


# ---------------------------------------------------------------------------
# 7. Order of checks — 3-strikes wins over AUTO-PROMOTE within a single iter
# ---------------------------------------------------------------------------

def test_three_strikes_block_comes_before_auto_promote() -> None:
    """If both fire in the same iter, the actionable failure handoff
    (which already requires user action) must take priority over the
    'just dispatch in background' nudge."""
    three_strikes_idx = _AGENT_SRC.index("# ── 3-strikes graceful handoff ──")
    auto_promote_idx = _AGENT_SRC.index("# ── Auto-promote-to-background nudge ──")
    assert three_strikes_idx < auto_promote_idx, (
        "3-strikes break must come before AUTO-PROMOTE in iter end-of-loop"
    )
