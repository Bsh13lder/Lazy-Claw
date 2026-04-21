"""Agent-visible read path for the cross-topic skill-lesson store.

Most lesson recall is automatic — `context_builder.py` injects topic-
matched exemplars on keyword hit, and `n8n_workflow_builder.py` pulls
past shapes before LLM generation. But small models can still end up
staring at a fresh task with no obvious escape route. This skill gives
them an explicit handle to ask: "what worked before for X?"

Read-only. Returns a compact markdown block (≤ ~2 KB) summarising up
to 5 past successes/fixes for the requested topic+intent.
"""

from __future__ import annotations

from typing import Any

from lazyclaw.skills.base import BaseSkill


class RecallTopicLessonsSkill(BaseSkill):
    def __init__(self, config: Any = None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "recall_topic_lessons"

    @property
    def category(self) -> str:
        return "memory"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Retrieve known-good past shapes for a given skill topic "
            "(n8n / instagram / email / whatsapp). Returns compact JSON "
            "exemplars from prior successful or fixed runs so you can "
            "reuse the working parameter shape without rediscovering it. "
            "Use before emitting complex tool calls — especially for n8n "
            "workflows or social/messaging tools — when you're unsure "
            "about required fields. Empty result = no prior runs on "
            "record; fall back to the tool's own schema hint."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Skill topic to recall for. Supported: "
                        "'n8n', 'instagram', 'email', 'whatsapp'."
                    ),
                },
                "intent": {
                    "type": "string",
                    "description": (
                        "1-line summary of what you're trying to do, "
                        "e.g. 'create google sheet named X'. Used for "
                        "semantic match against past lessons."
                    ),
                },
                "k": {
                    "type": "integer",
                    "description": "Max number of exemplars to return (1–5, default 3).",
                },
            },
            "required": ["topic", "intent"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        topic = str(params.get("topic", "")).strip().lower()
        intent = str(params.get("intent", "")).strip()
        k_raw = params.get("k", 3)
        try:
            k = max(1, min(5, int(k_raw)))
        except (TypeError, ValueError):
            k = 3

        if not topic or not intent:
            return "Error: both `topic` and `intent` are required."

        try:
            from lazyclaw.runtime.skill_lesson import (
                LEARNING_TOPICS,
                format_lessons_as_exemplars,
                recall_skill_lessons,
            )
        except Exception as exc:
            return f"Error: lesson store unavailable ({exc})."

        if topic not in LEARNING_TOPICS:
            return (
                f"Error: topic '{topic}' is not tracked. "
                f"Supported topics: {sorted(LEARNING_TOPICS)}."
            )

        lessons = await recall_skill_lessons(
            self._config, user_id,
            topic=topic, intent=intent, k=k,
        )
        if not lessons:
            return (
                f"No past lessons recorded yet for topic='{topic}' "
                f"matching intent='{intent}'. Proceed using the tool's "
                "own schema hints; a lesson will be written on success."
            )
        return format_lessons_as_exemplars(lessons)
