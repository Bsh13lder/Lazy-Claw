"""Self-healing path compiler — save successful browser paths, replay without LLM.

Skyvern-style route memorization:
1. After a successful browser task, compile the action sequence
2. On repeat tasks, replay the compiled path (no LLM needed)
3. If replay fails (site changed), fall back to LLM and recompile

Compiled paths are stored as encrypted site_memory entries with
type="compiled_path". Each path has:
- task_pattern: normalized task description for matching
- steps: ordered list of actions [{action, ref_role, ref_name, text, ...}]
- domain: the site domain
- success_count / fail_count: tracked by site_memory module

When fail_count > success_count + 2, the path is auto-deleted by site_memory.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.teams.learning import StepEntry

logger = logging.getLogger(__name__)

# ── Data models ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class CompiledStep:
    """Single step in a compiled browser path."""

    action: str          # "click", "type", "open", "press_key", "scroll", "wait"
    ref_role: str        # "button", "textbox", "link" (for re-finding element)
    ref_name: str        # Accessible name / text (for re-finding element)
    text: str            # Text to type (for "type" action)
    url: str             # URL to navigate (for "open" action)
    wait_seconds: float  # Wait time (for "wait" action)


@dataclass(frozen=True)
class CompiledPath:
    """Complete compiled browser path for a task on a domain."""

    task_pattern: str
    domain: str
    steps: tuple[CompiledStep, ...]
    success_count: int = 0
    fail_count: int = 0


# ── Compile from step history ────────────────────────────────────────

def _normalize_task(task: str) -> str:
    """Normalize a task description for pattern matching.

    Strips specific values (emails, names, numbers) to match similar tasks.
    Example: "Book appointment for john@email.com on 2024-01-15"
           → "book appointment for EMAIL on DATE"
    """
    normalized = task.lower().strip()
    # Replace emails
    normalized = re.sub(r'\S+@\S+\.\S+', 'EMAIL', normalized)
    # Replace dates (various formats)
    normalized = re.sub(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', 'DATE', normalized)
    normalized = re.sub(r'\d{1,2}[-/]\d{1,2}[-/]\d{2,4}', 'DATE', normalized)
    # Replace phone numbers
    normalized = re.sub(r'[\+]?\d[\d\s\-]{7,}', 'PHONE', normalized)
    # Replace specific numbers (prices, amounts)
    normalized = re.sub(r'[$€£¥₹]\s*[\d,.]+', 'AMOUNT', normalized)
    normalized = re.sub(r'[\d,.]+\s*[$€£¥₹]', 'AMOUNT', normalized)
    # Collapse whitespace
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized


def compile_path(
    step_history: tuple[StepEntry, ...],
    task: str,
    url: str,
) -> CompiledPath | None:
    """Compile a successful step history into a replayable path.

    Only compiles if:
    - At least 2 successful steps
    - Task involved browser actions (click, type, open)
    - URL is not empty

    Returns None if the history isn't worth compiling.
    """
    if not url or not step_history:
        return None

    # Filter to successful browser steps only
    browser_steps = [
        s for s in step_history
        if s.success and s.tool_name == "browser" and s.action
    ]

    if len(browser_steps) < 2:
        return None

    domain = urlparse(url).hostname or ""
    if not domain:
        return None

    compiled_steps: list[CompiledStep] = []
    for step in browser_steps:
        action = step.action or ""
        target = step.target or ""

        if action == "open":
            compiled_steps.append(CompiledStep(
                action="open",
                ref_role="",
                ref_name="",
                text="",
                url=target,
                wait_seconds=0,
            ))
        elif action == "click":
            # Extract role/name from target description
            compiled_steps.append(CompiledStep(
                action="click",
                ref_role="",  # Will be filled by snapshot matching
                ref_name=target,
                text="",
                url="",
                wait_seconds=0,
            ))
        elif action == "type":
            compiled_steps.append(CompiledStep(
                action="type",
                ref_role="",
                ref_name=target,
                text="",  # Don't save actual typed text (privacy)
                url="",
                wait_seconds=0,
            ))
        elif action == "press_key":
            compiled_steps.append(CompiledStep(
                action="press_key",
                ref_role="",
                ref_name=target,
                text="",
                url="",
                wait_seconds=0,
            ))
        elif action == "scroll":
            compiled_steps.append(CompiledStep(
                action="scroll",
                ref_role="",
                ref_name=target or "down",
                text="",
                url="",
                wait_seconds=0,
            ))

    if len(compiled_steps) < 2:
        return None

    return CompiledPath(
        task_pattern=_normalize_task(task),
        domain=domain,
        steps=tuple(compiled_steps),
    )


# ── Save/recall compiled paths ──────────────────────────────────────

async def save_compiled_path(
    config: Config,
    user_id: str,
    path: CompiledPath,
) -> str:
    """Save a compiled path to site memory."""
    from lazyclaw.browser.site_memory import remember

    steps_data = [
        {
            "action": s.action,
            "ref_role": s.ref_role,
            "ref_name": s.ref_name,
            "text": s.text,
            "url": s.url,
            "wait": s.wait_seconds,
        }
        for s in path.steps
    ]

    content = {
        "pattern": "compiled_path",
        "task_pattern": path.task_pattern,
        "steps": steps_data,
        "step_count": len(steps_data),
    }

    url = f"https://{path.domain}/"
    memory_id = await remember(
        config, user_id, url,
        memory_type="compiled_path",
        title=f"PATH: {path.task_pattern[:100]}",
        content=content,
    )

    logger.info(
        "Compiled path saved for %s: %d steps, pattern='%s'",
        path.domain, len(steps_data), path.task_pattern[:60],
    )
    return memory_id


async def find_compiled_path(
    config: Config,
    user_id: str,
    task: str,
    url: str,
) -> CompiledPath | None:
    """Find a compiled path matching this task and domain.

    Uses normalized task pattern matching — doesn't need exact match.
    Returns the path with highest success_count, or None.
    """
    from lazyclaw.browser.site_memory import recall

    memories = await recall(config, user_id, url)
    compiled_entries = memories.get("compiled_path", [])

    if not compiled_entries:
        return None

    task_pattern = _normalize_task(task)
    domain = urlparse(url).hostname or ""

    best_match: dict | None = None
    best_score = 0

    for entry in compiled_entries:
        content = entry.get("content", {})
        stored_pattern = content.get("task_pattern", "")

        # Simple similarity: count matching words
        task_words = set(task_pattern.split())
        stored_words = set(stored_pattern.split())
        common = len(task_words & stored_words)
        total = len(task_words | stored_words)
        score = common / max(total, 1)

        # Prefer higher success count
        success_bonus = min(entry.get("success_count", 0) * 0.05, 0.3)
        fail_penalty = entry.get("fail_count", 0) * 0.1
        final_score = score + success_bonus - fail_penalty

        if final_score > best_score and final_score > 0.5:
            best_score = final_score
            best_match = entry

    if not best_match:
        return None

    content = best_match.get("content", {})
    steps_data = content.get("steps", [])

    compiled_steps = tuple(
        CompiledStep(
            action=s.get("action", ""),
            ref_role=s.get("ref_role", ""),
            ref_name=s.get("ref_name", ""),
            text=s.get("text", ""),
            url=s.get("url", ""),
            wait_seconds=float(s.get("wait", 0)),
        )
        for s in steps_data
    )

    return CompiledPath(
        task_pattern=content.get("task_pattern", ""),
        domain=domain,
        steps=compiled_steps,
        success_count=best_match.get("success_count", 0),
        fail_count=best_match.get("fail_count", 0),
    )


async def mark_path_failed(
    config: Config,
    user_id: str,
    url: str,
    task_pattern: str,
) -> None:
    """Mark a compiled path as failed. Auto-deletes after too many failures."""
    from lazyclaw.browser.site_memory import mark_failed

    await mark_failed(
        config, user_id, url,
        memory_type="compiled_path",
        title=f"PATH: {task_pattern[:100]}",
    )
    logger.info("Marked compiled path as failed: %s", task_pattern[:60])
