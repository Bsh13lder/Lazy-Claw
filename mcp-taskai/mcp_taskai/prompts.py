from __future__ import annotations


def build_categorize_messages(task: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You are a task categorization expert. "
                "Categorize the given task into exactly one of these categories: "
                "work, personal, shopping, health, finance, learning, social, errands, other. "
                "Respond with ONLY valid JSON: "
                '{"category": "...", "confidence": 0.0-1.0}'
            ),
        },
        {"role": "user", "content": task},
    ]


def build_deadline_messages(task: str, priority: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You are a deadline estimation expert. "
                f"Given a task with priority '{priority}', suggest a realistic deadline. "
                "Consider urgency, complexity, and typical time needed. "
                "Today's tasks should be today, urgent within 1-2 days, "
                "normal within a week, low priority within 2 weeks. "
                "Respond with ONLY valid JSON: "
                '{"deadline": "YYYY-MM-DD", "reasoning": "..."}'
            ),
        },
        {"role": "user", "content": task},
    ]


def build_duplicates_messages(new_task: str, existing_tasks: list[str]) -> list[dict]:
    tasks_list = "\n".join(f"- {t}" for t in existing_tasks)
    return [
        {
            "role": "system",
            "content": (
                "You are a duplicate detection expert. "
                "Compare the new task against the existing task list. "
                "Identify tasks that are duplicates or very similar. "
                "Respond with ONLY valid JSON: "
                '{"duplicates": [{"task": "...", "similarity": 0.0-1.0, "reason": "..."}]}'
                "\nReturn an empty duplicates array if no matches found."
            ),
        },
        {
            "role": "user",
            "content": f"New task: {new_task}\n\nExisting tasks:\n{tasks_list}",
        },
    ]


def build_summarize_messages(tasks: list[str], summary_type: str) -> list[dict]:
    tasks_list = "\n".join(f"- {t}" for t in tasks)
    return [
        {
            "role": "system",
            "content": (
                f"You are a task summarization expert. Create a concise {summary_type} summary "
                "of the given tasks. Group related items, highlight priorities, "
                "and suggest what to tackle first. "
                "Respond with a well-formatted text summary (not JSON)."
            ),
        },
        {"role": "user", "content": f"Tasks:\n{tasks_list}"},
    ]


def build_prioritize_messages(tasks: list[str]) -> list[dict]:
    tasks_list = "\n".join(f"- {t}" for t in tasks)
    return [
        {
            "role": "system",
            "content": (
                "You are a prioritization expert. "
                "Order the given tasks by priority (1 = highest). "
                "Consider urgency, impact, dependencies, and effort. "
                "Respond with ONLY valid JSON: "
                '{"ordered": [{"task": "...", "priority": 1, "reasoning": "..."}]}'
            ),
        },
        {"role": "user", "content": f"Tasks to prioritize:\n{tasks_list}"},
    ]
