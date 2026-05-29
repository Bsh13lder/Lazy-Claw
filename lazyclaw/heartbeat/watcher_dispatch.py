"""Pure decision logic for what a browser watcher does on a detected change.

Extracted from ``HeartbeatDaemon._check_watchers`` so the routing rule is
unit-testable and so the DEFAULT is notification-only — the watcher tells you
what changed without commandeering your single live Brave.

Background (2026-05-29): a browser watcher used to ALWAYS fire a
browser-driving brain turn on change (default instruction
"Read the page, summarize what changed…"), because ``on_change_instruction``
was never written anywhere so the default always won. That brain turn drove
the user's live host Brave and stole the active tab from whatever foreground/
background task was using it (e.g. an in-flight Upwork proposal submit),
producing the "watcher always interrupts, doesn't open a new tab" bug.

This mirrors the MCP-watcher model (``_check_mcp_watchers``): always push the
zero-token diff the poller already built, and fire a brain turn ONLY when the
watcher was explicitly created with an ``on_change_instruction`` (opt-in).
"""

from __future__ import annotations

from dataclasses import dataclass

# accept_template_slug present → 🔔 + Accept/Skip buttons drive the next step.
ACTION_ACCEPT = "accept"
# Explicit on_change_instruction → drive a brain turn (opt-in; may use browser).
ACTION_BRAIN = "brain"
# Default → raw 🔔 notification only. NO browser turn, NO focus/tab steal.
ACTION_PUSH = "push"


@dataclass(frozen=True)
class OnChangeDecision:
    """Immutable result of the on-change routing decision."""

    action: str
    instruction: str | None = None


def decide_on_change_action(
    ctx: dict,
    new_ctx: dict,
    *,
    accept_slug: str | None,
    lane_running: bool,
) -> OnChangeDecision:
    """Decide how a browser watcher reacts to a detected change.

    Priority:
      1. ``accept_slug`` set                  → ``ACTION_ACCEPT`` (buttons).
      2. explicit ``on_change_instruction``   → ``ACTION_BRAIN`` (opt-in turn),
         but only when the lane queue is running to receive it.
      3. otherwise                            → ``ACTION_PUSH`` (notify only).

    ``ctx`` (the stored watcher context) takes precedence over ``new_ctx``
    (the freshly-polled context) for the instruction lookup.
    """
    if accept_slug:
        return OnChangeDecision(ACTION_ACCEPT, None)

    instr: str | None = None
    if "on_change_instruction" in ctx:
        instr = ctx.get("on_change_instruction")
    elif "on_change_instruction" in new_ctx:
        instr = new_ctx.get("on_change_instruction")

    if instr and instr.strip() and lane_running:
        return OnChangeDecision(ACTION_BRAIN, instr.strip())

    return OnChangeDecision(ACTION_PUSH, None)
