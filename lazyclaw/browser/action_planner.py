"""Browser Action Planner — decomposes browser tasks into planned steps.

Sits between the agent loop and browser_skill. When a browser task is
detected, a system message is injected asking the brain LLM to output a
BrowsingPlan as JSON before it acts. The planner then tracks execution
after each browser call and signals CONTINUE / REPLAN / ESCALATE.

Key design constraint: NO separate LLM call. The plan is a JSON block
the existing brain LLM embeds in its response content alongside the first
browser tool call. One LLM call, plan + first action together.

This module is pure Python — no async, no DB, no side effects.
All state is immutable (frozen dataclasses). Consumers call replace()
to advance state and inject the returned system messages.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ── Browser task detection ─────────────────────────────────────────────

# Keywords that suggest the user wants a multi-step browser task.
# Distinct from agent.py's _BROWSER_KEYWORDS (those control tool inclusion).
# These trigger the planning prompt for complex research/navigation tasks.
_PLANNING_KEYWORDS: frozenset[str] = frozenset({
    "research", "find", "look up", "look for", "search for",
    "buy", "purchase", "price of", "cost of",
    "open", "visit", "navigate", "go to",
    "compare", "review", "analyze",
    "what is", "how to", "latest", "news",
    "information about", "details about",
    "check if", "verify", "confirm",
    "sign in", "log in", "login",
    "fill", "submit", "form",
})

# Minimum word count for a message to be considered a browser task.
# Single-word commands like "google.com" skip planning.
_MIN_WORDS_FOR_PLAN = 4


def should_inject_plan(message: str, tool_call_history: list[str]) -> bool:
    """Return True if this message looks like a multi-step browser task.

    Rules:
    - Must have at least _MIN_WORDS_FOR_PLAN words (not a bare URL/command)
    - Must contain at least one planning keyword
    - Browser tool must not have been called yet (first browser encounter only)
    """
    if not message or len(message.split()) < _MIN_WORDS_FOR_PLAN:
        return False

    # If browser already used in this conversation turn, don't re-inject
    if "browser" in tool_call_history:
        return False

    message_lower = message.lower()
    return any(kw in message_lower for kw in _PLANNING_KEYWORDS)


# ── Data models ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannedStep:
    """A single planned browser action with verification and fallback."""

    description: str       # "Search for iPhone 16 price on Amazon"
    success_criteria: str  # "Price is visible on the page"
    fallback: str          # "Try Google Shopping if Amazon doesn't show price"


@dataclass(frozen=True)
class BrowsingPlan:
    """Immutable browsing plan. Use replace() to advance state."""

    goal: str
    steps: tuple[PlannedStep, ...]
    current_step: int = 0
    failed_steps: tuple[int, ...] = ()

    @property
    def current(self) -> PlannedStep | None:
        """Return the current step, or None if all steps done."""
        if self.current_step < len(self.steps):
            return self.steps[self.current_step]
        return None

    @property
    def is_complete(self) -> bool:
        return self.current_step >= len(self.steps)


class PlanStatus(Enum):
    CONTINUE = "continue"    # Step succeeded or neutral — keep going
    REPLAN = "replan"        # Step failed — inject fallback guidance
    ESCALATE = "escalate"    # Repeated failures — hand off to stuck detector


@dataclass(frozen=True)
class ActionPlannerState:
    """Per-conversation planner state. Immutable — use replace() to update."""

    plan: BrowsingPlan | None = None
    browser_call_count: int = 0
    consecutive_failures: int = 0
    plan_injected: bool = False


class PlannerDecision(NamedTuple):
    """Return value of evaluate_action_result."""

    new_state: ActionPlannerState
    status: PlanStatus
    system_message: str | None  # Inject into messages if not None


# ── Prompt construction ────────────────────────────────────────────────

_PLAN_JSON_SCHEMA = """{
  "goal": "<one-sentence goal>",
  "steps": [
    {
      "description": "<what to do>",
      "success_criteria": "<what success looks like>",
      "fallback": "<what to try if this fails>"
    }
  ]
}"""


def make_plan_injection_prompt(goal: str) -> str:
    """Return the system message to inject before the first browser call.

    The LLM should output the JSON plan as part of its response content,
    then include the first browser tool call. One LLM call total.
    """
    return (
        f"You're about to use the browser for: {goal}\n\n"
        f"Before your first browser action, output a brief plan as a JSON "
        f"block (3-5 steps). Use this exact schema:\n"
        f"```json\n{_PLAN_JSON_SCHEMA}\n```\n\n"
        f"Then immediately call the browser tool for step 1. "
        f"One response: plan JSON + first tool call."
    )


# ── Plan parsing ───────────────────────────────────────────────────────

# Matches ```json ... ``` or a raw { ... } JSON block in LLM response
_JSON_BLOCK_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```|(\{[^`]*\"steps\"\s*:\s*\[.*?\]\s*\})",
    re.DOTALL,
)


def parse_plan_from_response(content: str) -> BrowsingPlan | None:
    """Extract and parse a BrowsingPlan from LLM response content.

    Returns None if no valid plan block is found (non-browser responses).
    """
    if not content or "steps" not in content:
        return None

    match = _JSON_BLOCK_RE.search(content)
    if not match:
        # Try to find any JSON object containing "steps"
        start = content.find('{"goal"')
        if start == -1:
            start = content.find('{ "goal"')
        if start == -1:
            return None
        # Walk forward to find the matching closing brace
        brace_depth = 0
        end = start
        for i, ch in enumerate(content[start:], start):
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    end = i + 1
                    break
        raw = content[start:end]
    else:
        raw = match.group(1) or match.group(2)

    if not raw:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug("Plan JSON parse failed: %s", exc)
        return None

    goal = data.get("goal", "").strip()
    raw_steps = data.get("steps", [])

    if not goal or not raw_steps:
        return None

    steps: list[PlannedStep] = []
    for s in raw_steps:
        if not isinstance(s, dict):
            continue
        steps.append(PlannedStep(
            description=s.get("description", "").strip(),
            success_criteria=s.get("success_criteria", "").strip(),
            fallback=s.get("fallback", "Try a different approach").strip(),
        ))

    if not steps:
        return None

    logger.info(
        "Parsed browsing plan: goal=%r, steps=%d",
        goal[:60], len(steps),
    )
    return BrowsingPlan(goal=goal, steps=tuple(steps))


# ── Action result evaluation ───────────────────────────────────────────

# Signals in tool result text that indicate failure
_FAILURE_SIGNALS: tuple[str, ...] = (
    "error:", "failed:", "not found", "doesn't exist", "does not exist",
    "can't find", "cannot find", "no element", "not visible",
    "timed out", "connection refused", "404", "403", "500",
    "invalid", "blocked", "captcha", "access denied",
    "element not", "ref not", "no refs", "page is blank",
)

# Signals that indicate success / meaningful progress
_SUCCESS_SIGNALS: tuple[str, ...] = (
    "navigated to", "page:", "url:", "clicked", "typed",
    "snapshot", "elements found", "content extracted",
    "✓", "success", "loaded", "opened",
)


def _result_is_failure(result: str) -> bool:
    """Heuristic: does the tool result text indicate a failed action?"""
    lower = result.lower()
    return any(sig in lower for sig in _FAILURE_SIGNALS)


def evaluate_action_result(
    state: ActionPlannerState,
    action_name: str,
    result_text: str,
) -> PlannerDecision:
    """Evaluate a browser action result against the current plan step.

    Returns a PlannerDecision with updated state, status signal, and an
    optional system message to inject into the LLM context.

    Called after EVERY browser tool call (not just on failure).
    """
    new_call_count = state.browser_call_count + 1

    # No plan parsed yet — still useful to track call count
    if state.plan is None or state.plan.is_complete:
        new_state = replace(
            state,
            browser_call_count=new_call_count,
            consecutive_failures=0,
        )
        return PlannerDecision(new_state, PlanStatus.CONTINUE, None)

    plan = state.plan
    current_step = plan.current

    failed = _result_is_failure(result_text)

    if not failed:
        # Step succeeded — advance to next step, reset failure counter
        next_step_idx = plan.current_step + 1
        new_plan = replace(plan, current_step=next_step_idx)
        new_state = replace(
            state,
            plan=new_plan,
            browser_call_count=new_call_count,
            consecutive_failures=0,
        )

        # Inject step progress if there are more steps
        if next_step_idx < len(plan.steps):
            next_step = plan.steps[next_step_idx]
            msg = (
                f"Step {plan.current_step + 1}/{len(plan.steps)} done. "
                f"Next: {next_step.description}. "
                f"Success looks like: {next_step.success_criteria}"
            )
        else:
            msg = f"All {len(plan.steps)} planned steps complete. Synthesize your findings."

        return PlannerDecision(new_state, PlanStatus.CONTINUE, msg)

    # Step failed
    new_failures = state.consecutive_failures + 1
    new_failed_steps = plan.failed_steps + (plan.current_step,)
    new_plan = replace(plan, failed_steps=new_failed_steps)
    new_state = replace(
        state,
        plan=new_plan,
        browser_call_count=new_call_count,
        consecutive_failures=new_failures,
    )

    if new_failures >= 2:
        # Two consecutive failures — signal stuck detector to take over
        logger.warning(
            "Browser planner: %d consecutive failures on step %d — ESCALATE",
            new_failures, plan.current_step,
        )
        return PlannerDecision(new_state, PlanStatus.ESCALATE, None)

    # First failure — inject fallback hint
    fallback = current_step.fallback if current_step else "Try a different approach"
    msg = (
        f"Step {plan.current_step + 1} failed. "
        f"Try the fallback: {fallback}. "
        f"Use action='read' to understand the current page first."
    )
    logger.info(
        "Browser planner: step %d failed, injecting fallback hint",
        plan.current_step,
    )
    return PlannerDecision(new_state, PlanStatus.REPLAN, msg)
