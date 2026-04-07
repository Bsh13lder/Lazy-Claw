"""Task intelligence: categorization, prioritization, deduplication."""
from __future__ import annotations

import logging

from mcp_taskai.ai_client import AIClient
from mcp_taskai.prompts import (
    CATEGORIZE_PROMPT,
    DEADLINE_PROMPT,
    DUPLICATE_PROMPT,
    PRIORITIZE_PROMPT,
    SUMMARIZE_PROMPT,
)

logger = logging.getLogger(__name__)


class TaskIntelligence:
    """AI-powered task analysis using free LLM providers."""

    def __init__(self, ai_client: AIClient) -> None:
        self._ai = ai_client

    async def categorize(self, task: str) -> dict:
        """Categorize a task into a predefined category."""
        prompt = CATEGORIZE_PROMPT.format(task=task)
        return await self._ai.complete_json(prompt)

    async def prioritize(self, task: str, context: str = "") -> dict:
        """Assign a priority level to a task."""
        prompt = PRIORITIZE_PROMPT.format(task=task, context=context or "none")
        return await self._ai.complete_json(prompt)

    async def suggest_deadline(self, task: str, priority: str = "medium", created: str = "") -> dict:
        """Suggest a reasonable deadline for a task."""
        prompt = DEADLINE_PROMPT.format(
            task=task, priority=priority, created=created or "now",
        )
        return await self._ai.complete_json(prompt)

    async def detect_duplicates(self, new_task: str, existing_tasks: list[dict]) -> dict:
        """Check if a task is a duplicate of existing tasks."""
        tasks_text = "\n".join(
            f"- [{t.get('id', 'unknown')}] {t.get('title', t.get('task', ''))}"
            for t in existing_tasks
        )
        prompt = DUPLICATE_PROMPT.format(
            new_task=new_task, existing_tasks=tasks_text or "(none)",
        )
        return await self._ai.complete_json(prompt)

    async def summarize(self, tasks: list[dict], summary_type: str = "daily") -> str:
        """Summarize a list of tasks."""
        tasks_text = "\n".join(
            f"- [{t.get('status', '?')}] {t.get('title', t.get('task', ''))}"
            for t in tasks
        )
        prompt = SUMMARIZE_PROMPT.format(
            tasks=tasks_text or "(no tasks)",
            summary_type=summary_type,
        )
        return await self._ai.complete(prompt)
