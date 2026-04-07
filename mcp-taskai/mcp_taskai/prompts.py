"""Prompt templates for task intelligence."""
from __future__ import annotations

CATEGORIZE_PROMPT = """Categorize the following task into exactly one category.
Categories: work, personal, errands, health, finance, learning, creative, social, maintenance, other

Task: {task}

Respond with JSON: {{"category": "<category>", "confidence": <0.0-1.0>, "tags": ["<tag1>", ...]}}"""

PRIORITIZE_PROMPT = """Assign a priority to the following task.
Priorities: critical, high, medium, low

Task: {task}
Context: {context}

Respond with JSON: {{"priority": "<priority>", "reasoning": "<why>"}}"""

DEADLINE_PROMPT = """Suggest a reasonable deadline for this task.

Task: {task}
Priority: {priority}
Created: {created}

Respond with JSON: {{"suggested_deadline": "<ISO date>", "reasoning": "<why>", "estimated_hours": <number>}}"""

DUPLICATE_PROMPT = """Check if the new task is a duplicate or near-duplicate of any existing task.

New task: {new_task}

Existing tasks:
{existing_tasks}

Respond with JSON: {{"is_duplicate": <bool>, "similar_task_id": "<id or null>", "similarity": <0.0-1.0>}}"""

SUMMARIZE_PROMPT = """Summarize the following tasks into a {summary_type} summary.

Tasks:
{tasks}

Respond with a concise {summary_type} summary in 2-4 sentences."""
