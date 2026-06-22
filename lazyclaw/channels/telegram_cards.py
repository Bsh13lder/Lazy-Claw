"""Pure renderers for structured Telegram result cards.

Functions take the dicts the calling paths already hold (e.g. a task row) and
return the message BODY only. Inline keyboards stay at their existing call
sites — these never build markup.

Scope note: only surfaces that genuinely carry structured data at compose-time
get a card. Most Telegram-layer formatting points receive a pre-built string
(the reminder/contract notifier event flow), so broad card coverage needs the
structured ``view_hint`` pipeline (design doc, approach B). Today the reminder/
nag push is the one surface with the task fields in hand, so it gets a card.

Design: 2026-06-22-telegram-chat-visual-upgrade-design.md
"""
from __future__ import annotations

_PRIORITY_ICON = {
    "urgent": "\U0001f534",  # 🔴
    "high": "\U0001f7e0",    # 🟠
    "medium": "",
    "low": "\U0001f7e2",     # 🟢
}


def render_reminder_card(task: dict) -> str:
    """Render a task reminder / nag card.

    Expected keys (all optional except title): ``title``, ``priority``,
    ``category``, ``due_human`` (wall-clock time string), ``nag_count``.
    Preserves every field the legacy one-liner showed, in a cleaner two-line
    layout: a title line + a ``·``-separated metadata line.
    """
    title = task.get("title") or "Task"
    lines = [f"⏰ Reminder · {title}"]

    meta: list[str] = []
    priority = task.get("priority") or ""
    pri_icon = _PRIORITY_ICON.get(priority, "")
    if priority and priority != "medium":
        meta.append(f"{pri_icon} {priority}".strip())
    category = task.get("category")
    if category:
        meta.append(str(category))
    due = task.get("due_human") or task.get("reminder_human")
    if due:
        meta.append(f"⏰ {due}")
    try:
        nag_count = int(task.get("nag_count") or 0)
    except (TypeError, ValueError):
        nag_count = 0
    if nag_count > 0:
        meta.append(f"reminder #{nag_count + 1}")

    if meta:
        lines.append(" · ".join(meta))
    return "\n".join(lines)
