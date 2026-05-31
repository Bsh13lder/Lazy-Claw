"""Tests for browser-watcher on-change dispatch (2026-05-29).

Root cause being fixed: a browser watcher ALWAYS fired a browser-driving
brain turn on change (default instruction "Read the page, summarize what
changed…"), because ``on_change_instruction`` was never written anywhere in
the codebase so the default always won. That brain turn drove the user's
single live Brave and stole the active tab away from whatever foreground /
background task was using it (e.g. an in-flight Upwork proposal submit) —
the "watcher always interrupts, doesn't open a new tab" bug.

Fix: mirror the MCP-watcher opt-in model. Default to a notification-only
push (the passive poll already built a zero-token diff); fire a brain turn
ONLY when the watcher was explicitly created with an ``on_change_instruction``.
"""

from __future__ import annotations

import pytest

from lazyclaw.heartbeat.watcher_dispatch import (
    ACTION_ACCEPT,
    ACTION_BRAIN,
    ACTION_PUSH,
    decide_on_change_action,
)


# ── default fires a brain turn (regression fix 2026-05-30) ───────────


def test_default_fires_brain_with_default_instruction():
    """No on_change_instruction + running lane → brain turn with a non-empty
    DEFAULT instruction (NOT notify-only).

    Regression: commit 0509308 downgraded every pre-existing watcher to
    notify-only because the brand-new ``on_change_instruction`` field is
    never set on old watchers. The per-user live-Brave lock now prevents the
    tab-steal that motivated the over-correction, so the brain-turn default
    is restored. Custom ``on_change_instruction`` remains an override.
    """
    decision = decide_on_change_action(
        {"url": "https://www.upwork.com/ab/messages/rooms/room_x"},
        {},
        accept_slug=None,
        lane_running=True,
    )
    assert decision.action == ACTION_BRAIN
    assert decision.instruction
    assert decision.instruction.strip()


def test_default_instruction_falls_back_to_push_when_lane_down():
    """No instruction + no running lane → genuine notify-only (no brain turn)."""
    decision = decide_on_change_action(
        {"url": "https://www.upwork.com/ab/messages/rooms/room_x"},
        {},
        accept_slug=None,
        lane_running=False,
    )
    assert decision.action == ACTION_PUSH
    assert decision.instruction is None


def test_empty_instruction_falls_back_to_default_brain():
    """Whitespace-only on_change_instruction → fall back to the DEFAULT brain
    turn (not notify-only), since an empty override carries no intent."""
    for blank in ("", "   ", "\n\t"):
        decision = decide_on_change_action(
            {"on_change_instruction": blank, "url": "https://example.com"},
            {},
            accept_slug=None,
            lane_running=True,
        )
        assert decision.action == ACTION_BRAIN, blank
        assert decision.instruction and decision.instruction.strip(), blank


# ── opt-in: explicit instruction fires a brain turn ──────────────────


def test_explicit_instruction_fires_brain():
    decision = decide_on_change_action(
        {"on_change_instruction": "Reply to the new message politely."},
        {},
        accept_slug=None,
        lane_running=True,
    )
    assert decision.action == ACTION_BRAIN
    assert decision.instruction == "Reply to the new message politely."


def test_instruction_is_stripped():
    decision = decide_on_change_action(
        {"on_change_instruction": "  do the thing  "},
        {},
        accept_slug=None,
        lane_running=True,
    )
    assert decision.action == ACTION_BRAIN
    assert decision.instruction == "do the thing"


def test_instruction_from_new_ctx():
    """new_ctx (fresh poll context) can carry the instruction too."""
    decision = decide_on_change_action(
        {},
        {"on_change_instruction": "summarize and draft"},
        accept_slug=None,
        lane_running=True,
    )
    assert decision.action == ACTION_BRAIN
    assert decision.instruction == "summarize and draft"


def test_ctx_instruction_wins_over_new_ctx():
    decision = decide_on_change_action(
        {"on_change_instruction": "from ctx"},
        {"on_change_instruction": "from new_ctx"},
        accept_slug=None,
        lane_running=True,
    )
    assert decision.instruction == "from ctx"


# ── brain turn can't fire when the lane queue is down ────────────────


def test_instruction_but_lane_down_falls_back_to_push():
    decision = decide_on_change_action(
        {"on_change_instruction": "do the thing"},
        {},
        accept_slug=None,
        lane_running=False,
    )
    assert decision.action == ACTION_PUSH


# ── accept_slug wins over everything (contract-intake 1-tap) ─────────


def test_accept_slug_takes_priority():
    decision = decide_on_change_action(
        {"on_change_instruction": "do the thing"},
        {},
        accept_slug="estreet_bpo",
        lane_running=True,
    )
    assert decision.action == ACTION_ACCEPT
    assert decision.instruction is None
