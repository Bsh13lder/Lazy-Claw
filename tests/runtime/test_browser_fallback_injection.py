"""Tests for the browser-fallback re-injection after channel-MCP failures.

The bug being closed: when channel MCP tools are injected (any upwork /
whatsapp / instagram / email turn), ``browser`` + ``use_host_browser`` +
``run_command`` are added to ``_suppressed_tool_names`` at turn start
(MCP-first design) and the late-inject-from-registry path refuses to
resurrect them. When the channel MCP tool then fails repeatedly (e.g.
mcp-upwork dies mid-Cloudflare), the brain has NO path to the page at
all — the native ``browser`` skill drives the SAME signed-in Brave
profile the MCP uses, so it would pass Cloudflare with cookies intact,
but it can never come back this turn.

New contract pinned here:
  * 2nd consecutive failure of a channel-MCP tool while ``browser`` is
    suppressed → suppression lifted for ``browser``/``use_host_browser``
    (``run_command`` STAYS suppressed), the registry schema for
    ``browser`` is injected into the live tool list, and a short system
    message tells the brain the sanctioned fallback exists.
  * Fires at most ONCE per turn (``_browser_fallback_injected`` latch).
  * Non-channel tools never trigger it.
  * The 3-strikes handoff still fires at 3 failures — but is deferred
    while the fallback is pending injection so the brain always gets
    one LLM iteration with the browser before the handoff.

Behavioral tests cover the extracted helpers; the loop wiring is pinned
by static-source checks (same pattern as ``test_f1_retry_recheck.py`` /
``test_agent_dict_error_detection.py`` — the full agentic loop is too
heavy to mock).
"""

from __future__ import annotations

from pathlib import Path

from lazyclaw.runtime.agent import (
    _BROWSER_FALLBACK_AT_FAILURES,
    _is_channel_mcp_tool_name,
    _should_inject_browser_fallback,
)

_AGENT_SRC = (
    Path(__file__).parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
).read_text()


def _fallback_inject_block() -> str:
    """The post-loop block that performs the actual injection."""
    start = _AGENT_SRC.find("# ── Browser fallback re-injection ──")
    assert start != -1, "Browser fallback re-injection block not found"
    end = _AGENT_SRC.find("# ── 3-strikes graceful handoff ──", start)
    assert end != -1, "3-strikes block must follow the fallback block"
    return _AGENT_SRC[start:end]


def _failure_counter_block() -> str:
    """The per-tool failure counter block inside the tool-result loop."""
    start = _AGENT_SRC.find("# Per-tool consecutive-failure counter")
    assert start != -1, "failure counter block not found"
    end = _AGENT_SRC.find("await recorder.record_tool_result", start)
    assert end != -1
    return _AGENT_SRC[start:end]


# ─── Channel-MCP tool name predicate ─────────────────────────────────────


def test_upwork_tools_are_channel_mcp() -> None:
    assert _is_channel_mcp_tool_name("upwork_send_message") is True
    assert _is_channel_mcp_tool_name("upwork_get_conversation") is True
    assert _is_channel_mcp_tool_name("upwork_submit_proposal") is True


def test_mcp_uuid_wrapped_names_match() -> None:
    """MCP-wrapped names keep matching across subprocess restarts."""
    assert _is_channel_mcp_tool_name(
        "mcp_c2d0f293-ccf7-4987-a4dd-7edadc97261f_upwork_get_messages"
    ) is True
    assert _is_channel_mcp_tool_name(
        "mcp_aaaa-bbbb_whatsapp_send"
    ) is True


def test_other_channel_shapes_match() -> None:
    assert _is_channel_mcp_tool_name("whatsapp_send") is True
    assert _is_channel_mcp_tool_name("instagram_read_dms") is True
    assert _is_channel_mcp_tool_name("email_send") is True
    # Telegram is the native bot-API adapter (not browser-backed) — a
    # browser fallback can't help it, so it must NOT match.
    assert _is_channel_mcp_tool_name("telegram_get_messages") is False


def test_non_channel_tools_do_not_match() -> None:
    assert _is_channel_mcp_tool_name("browser") is False
    assert _is_channel_mcp_tool_name("add_task") is False
    assert _is_channel_mcp_tool_name("web_search") is False
    assert _is_channel_mcp_tool_name("run_background") is False
    assert _is_channel_mcp_tool_name("search_tools") is False


def test_empty_and_none_do_not_match() -> None:
    assert _is_channel_mcp_tool_name(None) is False
    assert _is_channel_mcp_tool_name("") is False


# ─── Fallback decision helper ────────────────────────────────────────────


def test_second_failure_triggers_fallback() -> None:
    """(a) 2nd failure of a channel tool with browser suppressed → fire."""
    assert _should_inject_browser_fallback(
        "upwork_get_conversation",
        2,
        {"browser", "use_host_browser", "run_command"},
        already_injected=False,
    ) is True


def test_first_failure_does_not_trigger() -> None:
    assert _should_inject_browser_fallback(
        "upwork_get_conversation",
        1,
        {"browser", "use_host_browser", "run_command"},
        already_injected=False,
    ) is False


def test_only_once_per_turn() -> None:
    """(b) the injected latch blocks any further firing this turn."""
    assert _should_inject_browser_fallback(
        "upwork_get_conversation",
        2,
        {"browser", "use_host_browser", "run_command"},
        already_injected=True,
    ) is False
    # Even a different channel tool can't re-fire after the latch.
    assert _should_inject_browser_fallback(
        "whatsapp_send",
        5,
        {"browser"},
        already_injected=True,
    ) is False


def test_non_channel_tool_does_not_trigger() -> None:
    """(c) repeated failures of non-channel tools never lift suppression."""
    assert _should_inject_browser_fallback(
        "web_search",
        2,
        {"browser", "use_host_browser", "run_command"},
        already_injected=False,
    ) is False
    assert _should_inject_browser_fallback(
        "add_task",
        4,
        {"browser"},
        already_injected=False,
    ) is False


def test_no_trigger_when_browser_not_suppressed() -> None:
    """If browser is already available there's nothing to re-inject."""
    assert _should_inject_browser_fallback(
        "upwork_get_conversation",
        2,
        set(),
        already_injected=False,
    ) is False


def test_threshold_constant_is_two() -> None:
    """Fallback at 2 + three-strikes at 3 = one guaranteed LLM iteration
    with the browser available before the graceful handoff."""
    assert _BROWSER_FALLBACK_AT_FAILURES == 2


def test_late_trigger_still_fires_past_threshold() -> None:
    """>= semantics: if the 2-count tick was missed (e.g. suppression
    state changed), a later failure still fires the fallback."""
    assert _should_inject_browser_fallback(
        "upwork_get_conversation",
        3,
        {"browser"},
        already_injected=False,
    ) is True


# ─── Loop wiring (static-source checks) ──────────────────────────────────


def test_trigger_wired_into_failure_counter() -> None:
    """The decision helper must be called from the err-result counter
    branch, BEFORE the three-strikes check."""
    block = _failure_counter_block()
    assert "_should_inject_browser_fallback(" in block
    assert "_browser_fallback_pending = (_short, result[-300:])" in block
    # Ordering: fallback trigger before the three-strikes assignment so a
    # same-batch 2nd+3rd failure defers the handoff one iteration.
    assert (
        block.index("_should_inject_browser_fallback(")
        < block.index("_three_strikes_break = (_short, result[-300:])")
    )


def test_run_command_stays_suppressed() -> None:
    """(d) only browser + use_host_browser come back — shell stays out."""
    block = _fallback_inject_block()
    assert '-= {"browser", "use_host_browser"}' in block
    code_only = "\n".join(
        line for line in block.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "run_command" not in code_only, (
        "run_command must NOT be lifted from _suppressed_tool_names — "
        "SOUL.md forbids shell as a channel workaround"
    )


def test_schema_injected_from_registry_not_hand_rolled() -> None:
    """The browser schema must come from the skill registry — the same
    mechanism the late-inject path uses — never a hand-rolled dict."""
    block = _fallback_inject_block()
    assert 'get_tool_schema("browser")' in block
    assert "tools.append(" in block


def test_system_message_appended_with_guidance() -> None:
    """The brain must be TOLD the fallback exists and how to start."""
    block = _fallback_inject_block()
    assert "messages.append(LLMMessage(" in block
    assert 'role="system"' in block
    assert "signed-in Brave" in block
    assert 'browser(action=\\"open\\"' in block.replace("'", '"') or (
        'action="open"' in block or 'action=\\"open\\"' in block
    )


def test_info_log_marker_present() -> None:
    block = _fallback_inject_block()
    assert "Browser fallback injected after 2 failures of %s" in block


def test_once_per_turn_latch_set_in_inject_block() -> None:
    block = _fallback_inject_block()
    assert "_browser_fallback_injected = True" in block


def test_per_turn_state_initialized() -> None:
    """Both per-turn locals must be initialized next to the 3-strikes
    state so every turn starts clean."""
    assert "_browser_fallback_pending: tuple[str, str] | None = None" in _AGENT_SRC
    assert "_browser_fallback_injected = False" in _AGENT_SRC


def test_three_strikes_still_fires_at_three() -> None:
    """(e) the 3-strikes handoff is intact — count >= 3 still queues the
    graceful handoff (after the fallback had its chance)."""
    block = _failure_counter_block()
    assert "_tool_failure_count[_short] >= 3" in block
    assert "_three_strikes_break = (_short, result[-300:])" in block


def test_three_strikes_deferred_while_fallback_pending() -> None:
    """A same-batch 2nd+3rd failure must NOT exit the turn before the
    brain sees the fallback message — three-strikes waits until the
    pending injection has been flushed."""
    block = _failure_counter_block()
    start = block.index("_tool_failure_count[_short] >= 3")
    end = block.index("_three_strikes_break = (_short", start)
    condition = block[start:end]
    assert "_browser_fallback_pending is None" in condition, (
        "three-strikes condition must defer while the browser fallback "
        "is pending injection"
    )
