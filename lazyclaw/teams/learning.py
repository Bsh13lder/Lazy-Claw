"""Specialist learning — extract and save browser learnings to site memory.

After a browser specialist completes (success or failure), this module
analyzes the step history to find reusable patterns and persists them
as encrypted site memories for future visits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from lazyclaw.config import Config

logger = logging.getLogger(__name__)

_MAX_LEARNINGS = 3
_MAX_TITLE_LEN = 200
MIN_STEPS_FOR_LEARNING = 3

# Error keywords → pattern names for structured detection
_ERROR_PATTERNS: dict[str, str] = {
    "element not found": "missing_element",
    "timeout": "slow_page",
    "not clickable": "unclickable_element",
    "blocked": "access_blocked",
    "not visible": "hidden_element",
}


@dataclass(frozen=True)
class StepEntry:
    """Immutable record of a single specialist tool execution."""

    tool_name: str
    action: str | None
    target: str | None
    success: bool
    error_snippet: str
    iteration: int


@dataclass(frozen=True)
class LearningEntry:
    """Immutable learning extracted from specialist step history."""

    title: str
    content: dict[str, object]


def _find_last_url(step_history: tuple[StepEntry, ...]) -> str | None:
    """Find the most recent URL from browser open/read actions."""
    for step in reversed(step_history):
        if step.action in ("open", "read") and step.target:
            if step.target.startswith(("http://", "https://")):
                return step.target
    return None


def _extract_learnings(
    step_history: tuple[StepEntry, ...],
    task: str,
    success: bool,
    error: str | None,
) -> list[LearningEntry]:
    """Pure function: analyze steps, return max 3 learnings.

    Detection strategy:
    1. Repeated failures — 2+ *consecutive* failures on the same tool+action.
       Alternating failures on different actions (A-fail, B-fail, A-fail) do NOT
       trigger, because interleaved failures usually indicate exploration, not a
       stuck loop.
    2. Successful flow — ordered summary of actions that succeeded.
    3. Error patterns — match common actionable error keywords.
    """
    learnings: list[LearningEntry] = []

    # 1. Repeated failures: 2+ consecutive failures on same action
    prev_fail_key: str | None = None
    fail_streak = 0
    for step in step_history:
        key = f"{step.tool_name}:{step.action}"
        if not step.success:
            if key == prev_fail_key:
                fail_streak += 1
            else:
                prev_fail_key = key
                fail_streak = 1
            if fail_streak == 2:
                learnings.append(LearningEntry(
                    title=f"AVOID: {step.action} repeated with same failure"[:_MAX_TITLE_LEN],
                    content={
                        "pattern": "repeated_failure",
                        "action": step.action,
                        "error": step.error_snippet[:200],
                        "task_context": task[:100],
                    },
                ))
                break  # One repeated-failure learning is enough
        else:
            prev_fail_key = None
            fail_streak = 0

    # 2. Successful flow: summarize what worked
    if success:
        successful_actions = [
            f"{s.action}({s.target[:40] if s.target else ''})"
            for s in step_history
            if s.success and s.action
        ]
        if len(successful_actions) >= 2:
            flow = " -> ".join(successful_actions[:8])
            learnings.append(LearningEntry(
                title=f"WORKS: {flow}"[:_MAX_TITLE_LEN],
                content={
                    "pattern": "successful_flow",
                    "steps": successful_actions[:8],
                    "task_context": task[:100],
                },
            ))

    # 3. Error patterns: match common actionable errors
    seen_patterns: set[str] = set()
    for step in step_history:
        if not step.success:
            snippet_lower = step.error_snippet.lower()
            for keyword, pattern_name in _ERROR_PATTERNS.items():
                if keyword in snippet_lower and pattern_name not in seen_patterns:
                    seen_patterns.add(pattern_name)
                    learnings.append(LearningEntry(
                        title=(
                            f"{pattern_name}: {step.action} on "
                            f"{step.target or 'page'}"
                        )[:_MAX_TITLE_LEN],
                        content={
                            "pattern": pattern_name,
                            "action": step.action,
                            "error": step.error_snippet[:200],
                        },
                    ))

    return learnings[:_MAX_LEARNINGS]


async def save_browser_learnings(
    config: Config,
    user_id: str,
    step_history: tuple[StepEntry, ...],
    task: str,
    success: bool,
    error: str | None,
) -> None:
    """Fire-and-forget: save what the browser specialist learned.

    Extracts patterns from step history and persists them as encrypted
    site memories. Never raises — all errors caught and logged.
    """
    try:
        url = _find_last_url(step_history)
        if not url:
            return

        domain = urlparse(url).hostname or ""
        if not domain:
            return

        learnings = _extract_learnings(step_history, task, success, error)
        if not learnings:
            return

        from lazyclaw.browser.site_memory import remember

        for learning in learnings:
            try:
                await remember(
                    config,
                    user_id,
                    url,
                    memory_type="site_learning",
                    title=learning.title,
                    content=learning.content,
                )
            except Exception:
                logger.debug(
                    "Failed to save learning '%s' for %s",
                    learning.title[:60], domain,
                    exc_info=True,
                )

        logger.info(
            "Saved %d browser learnings for %s (success=%s)",
            len(learnings), domain, success,
        )

        # Compile successful paths for replay without LLM
        if success and len(step_history) >= 3:
            try:
                from lazyclaw.browser.path_compiler import compile_path, save_compiled_path

                compiled = compile_path(step_history, task, url)
                if compiled:
                    await save_compiled_path(config, user_id, compiled)
                    logger.info(
                        "Compiled path saved for %s (%d steps)",
                        domain, len(compiled.steps),
                    )
            except Exception:
                logger.debug("Path compilation failed", exc_info=True)

    except Exception:
        logger.debug("Specialist learning save failed", exc_info=True)
