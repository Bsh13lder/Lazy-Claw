"""Dropped-channel confabulation guard (2026-07-04 web Upwork incident).

Production incident 2026-07-04: from web, the user asked "check my upwork
what's new". Brain = MiniMax-M2.7. It called ``browser`` (dropped as
hallucinated — not in current tools), retried, called ``upwork_inbox_check``
(also dropped), then fell back to a text-only reply and FABRICATED an inbox
summary: "The MCP detected 1 conversation room … | Sender | Room ID |
Unread Count | … | Amit K | …". The real inbox was EMPTY — no Upwork tool
ever ran successfully this turn. The fabrication shipped to the user.

Why the existing F1 grounding gates missed it: both are guarded by
``any(_is_channel_read_tool(n) for n in _tool_call_history)`` — i.e. they
only fire when a channel-read tool ACTUALLY RAN. Here the channel tools
were REQUESTED but DROPPED (never ran, never in ``_tool_call_history``), so
the turn was treated as a non-channel turn and enforcement stayed
observation-only. The model had ZERO live channel data yet asserted
channel contents.

These tests pin the pure decision helpers the hot-path loop in
``lazyclaw/runtime/agent.py`` calls, plus the source-level wiring that
threads them through the drop site and the ship path. Structural invariant:
when the brain requested a channel-read tool that got dropped as
hallucinated AND no channel-read tool ran successfully this turn, it has NO
live channel data — so it must NOT report any inbox/messages/senders/counts.
It must delegate or honestly say it couldn't access the channel.
"""

from __future__ import annotations

from pathlib import Path

from lazyclaw.runtime.agent import (
    _DROPPED_CHANNEL_FALLBACK,
    _F1_DROPPED_CHANNEL_MAX_RETRIES,
    _build_dropped_channel_correction,
    _draft_asserts_channel_data,
    _dropped_channel_confab_guard_active,
    _dropped_channel_ship_decision,
    _is_channel_read_tool,
)


# ── the exact shape of the 2026-07-04 fabrication ─────────────────────────
_FABRICATED_INBOX = (
    "The MCP detected 1 conversation room in your Upwork inbox.\n\n"
    "| Sender | Room ID | Unread Count |\n"
    "| --- | --- | --- |\n"
    "| Amit K | room_abc123 | 1 |\n\n"
    "You have a new message from Amit K."
)

# An honest reply the brain COULD have produced on the same turn.
_HONEST_NO_ACCESS = (
    "I wasn't able to read your Upwork inbox just now — the read didn't go "
    "through. Want me to try again, or hand it to the specialist?"
)


# ── constants ─────────────────────────────────────────────────────────────


def test_max_retries_constant_is_two() -> None:
    """Cap mirrors the sibling F1 retry caps (2 grounding corrections)."""
    assert _F1_DROPPED_CHANNEL_MAX_RETRIES == 2


def test_fallback_is_honest_and_asserts_no_channel_data() -> None:
    """The safe fallback must NOT itself fabricate channel contents."""
    assert _DROPPED_CHANNEL_FALLBACK
    assert _draft_asserts_channel_data(_DROPPED_CHANNEL_FALLBACK) is False


# ── _dropped_channel_confab_guard_active ──────────────────────────────────


def test_guard_active_on_dropped_channel_no_run() -> None:
    """The incident shape: channel read requested-but-dropped, none ran."""
    assert _dropped_channel_confab_guard_active(
        _FABRICATED_INBOX, True, [],
    ) is True


def test_guard_noop_when_flag_false() -> None:
    """No channel tool was dropped this turn → guard never engages.

    This is the common case (the vast majority of turns) — the guard must
    be a provable no-op.
    """
    assert _dropped_channel_confab_guard_active(
        _FABRICATED_INBOX, False, [],
    ) is False


def test_guard_noop_when_a_channel_tool_actually_ran() -> None:
    """Real live data present → normal F1 gates own it, not this guard.

    Even if some other channel tool was dropped, the presence of a
    channel-read tool in the history means the brain HAS live data.
    """
    assert _dropped_channel_confab_guard_active(
        _FABRICATED_INBOX, True, ["upwork_get_messages"],
    ) is False


def test_guard_noop_when_mcp_prefixed_channel_tool_ran() -> None:
    """MCP-bridged channel reads count as 'a channel tool ran'."""
    history = ["mcp_aa828e97-7923-4189-b6e4-1f2ace89b115_upwork_get_messages"]
    assert _dropped_channel_confab_guard_active(
        _FABRICATED_INBOX, True, history,
    ) is False


def test_guard_noop_on_empty_content() -> None:
    """An empty draft makes no claims — nothing to guard."""
    assert _dropped_channel_confab_guard_active("", True, []) is False


def test_guard_noop_when_only_non_channel_tools_ran() -> None:
    """Non-channel tools in history don't count as a channel read, but the
    guard still only engages when the requested-but-dropped flag is set."""
    # flag False → no-op even though only web_search ran
    assert _dropped_channel_confab_guard_active(
        _FABRICATED_INBOX, False, ["web_search", "recall_memories"],
    ) is False


# ── _draft_asserts_channel_data ───────────────────────────────────────────


def test_asserts_on_fabricated_inbox_table() -> None:
    """The golden fabrication clearly asserts channel contents."""
    assert _draft_asserts_channel_data(_FABRICATED_INBOX) is True


def test_asserts_on_conversation_room_phrase() -> None:
    assert _draft_asserts_channel_data(
        "The MCP detected 1 conversation room.",
    ) is True


def test_asserts_on_unread_count_and_message_from() -> None:
    assert _draft_asserts_channel_data("Unread Count: 3") is True
    assert _draft_asserts_channel_data(
        "You have a new message from Sarah.",
    ) is True


def test_asserts_on_sender_room_table_row() -> None:
    """A markdown table row with channel-ish headers is a fabrication tell."""
    assert _draft_asserts_channel_data(
        "| Sender | Room ID | Unread |\n",
    ) is True


def test_no_assert_on_honest_couldnt_access() -> None:
    """An honest 'couldn't read' reply must NOT be treated as fabrication.

    Even though it mentions the inbox, the inability phrasing means it
    reports no invented contents — ship it unchanged.
    """
    assert _draft_asserts_channel_data(_HONEST_NO_ACCESS) is False


def test_no_assert_on_unable_variants() -> None:
    for reply in (
        "I couldn't access your Upwork inbox.",
        "I was not able to read the conversation — no access.",
        "Unable to reach Upwork; the read failed to load.",
    ):
        assert _draft_asserts_channel_data(reply) is False, reply


def test_no_assert_on_empty_or_generic_chat() -> None:
    assert _draft_asserts_channel_data("") is False
    assert _draft_asserts_channel_data(
        "Sure — what would you like me to do next?",
    ) is False


# ── _build_dropped_channel_correction ─────────────────────────────────────


def test_correction_is_system_framed_and_forbids_invention() -> None:
    msg = _build_dropped_channel_correction()
    assert msg.startswith("[SYSTEM:")
    assert msg.rstrip().endswith("]")
    low = msg.lower()
    # Must tell the model it has no live data and must not invent contents.
    assert "delegate" in low
    assert any(w in low for w in ("invent", "fabricate", "make up"))
    assert any(w in low for w in ("message", "sender", "inbox", "conversation"))


# ── _dropped_channel_ship_decision (the composed lifecycle) ───────────────


def test_decision_ships_unchanged_when_guard_inactive() -> None:
    """Flag False → identity ship (byte-for-byte no-op on unaffected turns)."""
    reply = "Here's the summary you asked for."
    action, payload = _dropped_channel_ship_decision(
        reply, False, ["web_search"], 0,
    )
    assert action == "ship"
    assert payload is reply  # identity — provably unchanged


def test_decision_ships_unchanged_on_legit_channel_turn() -> None:
    """A channel tool ran → decision is always 'ship' regardless of retries."""
    reply = (
        "> James (10:37 PM): We need an auto-accept bot\nHere's the plan."
    )
    action, payload = _dropped_channel_ship_decision(
        reply, True, ["upwork_get_messages"], 0,
    )
    assert action == "ship"
    assert payload == reply


def test_decision_retries_while_budget_remains() -> None:
    action0, _ = _dropped_channel_ship_decision(_FABRICATED_INBOX, True, [], 0)
    action1, _ = _dropped_channel_ship_decision(_FABRICATED_INBOX, True, [], 1)
    assert action0 == "retry"
    assert action1 == "retry"


def test_decision_replaces_fabrication_after_cap() -> None:
    action, payload = _dropped_channel_ship_decision(
        _FABRICATED_INBOX, True, [], _F1_DROPPED_CHANNEL_MAX_RETRIES,
    )
    assert action == "replace"
    assert payload == _DROPPED_CHANNEL_FALLBACK
    assert "Amit K" not in payload


def test_decision_ships_honest_reply_after_cap_unchanged() -> None:
    """Retries exhausted but the draft is already honest → ship it as-is."""
    action, payload = _dropped_channel_ship_decision(
        _HONEST_NO_ACCESS, True, [], _F1_DROPPED_CHANNEL_MAX_RETRIES,
    )
    assert action == "ship"
    assert payload == _HONEST_NO_ACCESS


# ── golden incident: the fabricated table can never ship ──────────────────


def test_golden_incident_never_ships_fabricated_inbox() -> None:
    """Walk the full retry lifecycle exactly as the loop would.

    On every step the fabricated inbox must be re-rolled or replaced —
    never shipped. After the retry budget is spent, the honest fallback
    ships and the invented 'Amit K / conversation room' table is gone.
    """
    retries = 0
    shipped: str | None = None
    final_action = ""
    for _ in range(_F1_DROPPED_CHANNEL_MAX_RETRIES + 3):
        action, payload = _dropped_channel_ship_decision(
            _FABRICATED_INBOX, True, [], retries,
        )
        final_action = action
        if action == "retry":
            retries += 1
            continue
        shipped = payload
        break

    assert shipped is not None
    assert final_action == "replace"
    assert "Amit K" not in shipped
    assert "conversation room" not in shipped.lower()
    assert shipped == _DROPPED_CHANNEL_FALLBACK


def test_is_channel_read_tool_recognizes_incident_tools() -> None:
    """Sanity: the tools dropped in the incident are channel reads."""
    assert _is_channel_read_tool("upwork_inbox_check") is True
    assert _is_channel_read_tool("upwork_get_messages") is True
    # ``browser`` is NOT a channel read — dropping it alone must not arm
    # the guard (the incident armed it via upwork_inbox_check).
    assert _is_channel_read_tool("browser") is False


# ── source-level wiring (mirrors tests/runtime/test_agent_force_dispatch) ──

_AGENT_SRC = (
    Path(__file__).parent.parent.parent
    / "lazyclaw" / "runtime" / "agent.py"
).read_text()


def test_turn_scoped_state_initialised() -> None:
    """Per-turn flag + retry counter allocated once at turn start."""
    assert "_channel_read_requested_but_dropped = False" in _AGENT_SRC
    assert "_f1_dropped_channel_retries = 0" in _AGENT_SRC


def test_flag_set_at_drop_site() -> None:
    """The sticky flag is armed where hallucinated tool calls are dropped."""
    idx = _AGENT_SRC.index("Dropped %d hallucinated tool calls")
    nearby = _AGENT_SRC[idx : idx + 1000]
    assert "_channel_read_requested_but_dropped = True" in nearby
    assert "_is_channel_read_tool(n) for n in _dropped_names" in nearby


def test_guard_wired_before_f1_phase1_gate() -> None:
    """The ship decision is consulted before the existing F1 phase-1 gate."""
    guard_idx = _AGENT_SRC.index("_dropped_channel_ship_decision(")
    f1_gate_idx = _AGENT_SRC.index("_f1_retries < _F1_MAX_RETRIES")
    assert guard_idx < f1_gate_idx


def test_replace_branch_overrides_shipped_content() -> None:
    """The last-resort replace must overwrite BOTH the user-facing text and
    the history content that actually gets appended + returned."""
    assert '_dc_action == "replace"' in _AGENT_SRC
    assert "_final_content = _dc_payload" in _AGENT_SRC
    assert "_history_content = _dc_payload" in _AGENT_SRC


def test_degraded_marker_logged() -> None:
    """A greppable WARN marker fires when the honest fallback is forced."""
    assert "[F1-dropped-channel-degraded]" in _AGENT_SRC
