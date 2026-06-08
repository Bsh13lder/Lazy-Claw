"""Pure decision logic for what a browser watcher does on a detected change.

Extracted from ``HeartbeatDaemon._check_watchers`` so the routing rule is
unit-testable.

Background (2026-05-29 → 2026-05-30): a browser watcher used to ALWAYS fire a
browser-driving brain turn on change (default instruction
"Read the page, summarize what changed…"). Commit 0509308 over-corrected this
to notification-only by default — firing a brain turn ONLY when the watcher
carried an explicit ``on_change_instruction``. But that field is brand-new and
was never written on any pre-existing watcher, so EVERY existing watcher was
silently downgraded to notify-only (regression: the user's "Watch: For new
messige" watcher stopped enqueuing brain turns on change).

The original motivation for notify-only was a tab-steal: the brain turn drove
the user's single live Brave and stole the active tab from a foreground/
background task (e.g. an in-flight Upwork proposal submit). That tab-steal is
now prevented by the per-user live-Brave lock (``browser_turn_lock.py``) —
watcher polls serialize through the lock and skip when busy — so restoring the
brain-turn DEFAULT is safe again.

Current contract:
  * ``accept_slug`` set                  → ``ACTION_ACCEPT`` (1-tap buttons).
  * explicit ``on_change_instruction``   → ``ACTION_BRAIN`` (custom override).
  * no instruction + running lane        → ``ACTION_BRAIN`` (DEFAULT turn).
  * no running lane / brain not possible  → ``ACTION_PUSH`` (notify only).
"""

from __future__ import annotations

from dataclasses import dataclass

# accept_template_slug present → 🔔 + Accept/Skip buttons drive the next step.
ACTION_ACCEPT = "accept"
# Brain turn on change — either an explicit on_change_instruction (override)
# or the restored DEFAULT instruction (may drive the browser).
ACTION_BRAIN = "brain"
# Genuine notify-only — used when no lane is running so a brain turn can't fire.
ACTION_PUSH = "push"


@dataclass(frozen=True)
class OnChangeDecision:
    """Immutable result of the on-change routing decision."""

    action: str
    instruction: str | None = None


def default_on_change_instruction(job_name: str, url: str) -> str:
    """Build the DEFAULT on-change instruction used when a watcher has no
    explicit ``on_change_instruction``.

    Recovered verbatim from the pre-0509308 ``daemon.py`` default so behaviour
    matches the long-standing watcher contract.
    """
    return (
        f"Watcher '{job_name}' detected new content "
        f"at {url}. Read the page, "
        f"summarize what changed, and route per "
        f"normal rules (auto-reply where safe, "
        f"escalate sensitive items, draft for "
        f"active deals). Stay terse."
    )


def decide_on_change_action(
    ctx: dict,
    new_ctx: dict,
    *,
    accept_slug: str | None,
    lane_running: bool,
    job_name: str = "watcher",
) -> OnChangeDecision:
    """Decide how a browser watcher reacts to a detected change.

    Priority:
      1. ``accept_slug`` set                  → ``ACTION_ACCEPT`` (buttons).
      2. explicit ``on_change_instruction``   → ``ACTION_BRAIN`` (custom
         override), but only when the lane queue is running to receive it.
      3. no/empty instruction + running lane  → ``ACTION_BRAIN`` with the
         restored DEFAULT instruction (the historical watcher behaviour).
      4. lane not running                     → ``ACTION_PUSH`` (notify only),
         since no brain turn can be enqueued.

    ``ctx`` (the stored watcher context) takes precedence over ``new_ctx``
    (the freshly-polled context) for the instruction and url lookups.
    """
    if accept_slug:
        return OnChangeDecision(ACTION_ACCEPT, None)

    # Without a running lane queue we cannot enqueue a brain turn at all, so
    # this is the genuine notify-only case (the passive poll already built a
    # zero-token diff for the caller to push).
    if not lane_running:
        return OnChangeDecision(ACTION_PUSH, None)

    instr: str | None = None
    if "on_change_instruction" in ctx:
        instr = ctx.get("on_change_instruction")
    elif "on_change_instruction" in new_ctx:
        instr = new_ctx.get("on_change_instruction")

    # Explicit instruction wins as a custom override.
    if instr and instr.strip():
        return OnChangeDecision(ACTION_BRAIN, instr.strip())

    # No (or empty) override → restored DEFAULT brain turn.
    url = ctx.get("url") or new_ctx.get("url") or ""
    return OnChangeDecision(
        ACTION_BRAIN,
        default_on_change_instruction(job_name, url),
    )
