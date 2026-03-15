from __future__ import annotations

import json
import logging
import re

from mcp_taskai.ai_client import AIClient
from mcp_taskai.prompts import (
    build_categorize_messages,
    build_deadline_messages,
    build_duplicates_messages,
    build_prioritize_messages,
    build_summarize_messages,
)

logger = logging.getLogger(__name__)


class TaskIntelligence:
    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def categorize(self, task: str) -> dict:
        messages = build_categorize_messages(task)
        raw = await self._client.chat(messages)
        return self._parse_json(raw)

    async def suggest_deadline(self, task: str, priority: str = "medium") -> dict:
        messages = build_deadline_messages(task, priority)
        raw = await self._client.chat(messages)
        return self._parse_json(raw)

    async def detect_duplicates(
        self, new_task: str, existing_tasks: list[str]
    ) -> dict:
        if not existing_tasks:
            return {"duplicates": []}
        messages = build_duplicates_messages(new_task, existing_tasks)
        raw = await self._client.chat(messages)
        return self._parse_json(raw)

    async def summarize(
        self, tasks: list[str], summary_type: str = "overdue"
    ) -> str:
        if not tasks:
            return "No tasks to summarize."
        messages = build_summarize_messages(tasks, summary_type)
        return await self._client.chat(messages)

    async def prioritize(self, tasks: list[str]) -> dict:
        if not tasks:
            return {"ordered": []}
        messages = build_prioritize_messages(tasks)
        raw = await self._client.chat(messages)
        return self._parse_json(raw)

    def _parse_json(self, text: str) -> dict:
        """Extract JSON from AI response, handling markdown code fences."""
        cleaned = text.strip()

        # Strip markdown code fences if present
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", cleaned, re.DOTALL)
        if fence_match:
            cleaned = fence_match.group(1).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON from AI response: %s", text[:200])
            return {"error": "Failed to parse AI response", "raw": text}
