"""Pure renderers for structured Telegram result cards.

Functions take the dicts the calling paths already hold (task rows, contract
dicts, permission maps) and return the message BODY only. Inline keyboards stay
at their existing call sites — these never build markup.

Design: 2026-06-22-telegram-chat-visual-upgrade-design.md
"""
from __future__ import annotations


def render_reminder_card(task: dict) -> str:
    title = task.get("title", "Task")
    lines = [f"⏰ Reminder · {title}"]
    due = task.get("due_human") or task.get("reminder_human")
    if due:
        lines.append(f"Due {due}")
    return "\n".join(lines)


def render_contract_card(contract: dict) -> str:
    title = contract.get("title", "Contract")
    lines = ["\U0001f514 New contract", title]
    budget = contract.get("budget")
    if budget:
        lines.append(f"Budget {budget}")
    return "\n".join(lines)


def render_permissions_card(perms: dict) -> str:
    rows = [
        ("✅ Allowed", perms.get("allow", [])),
        ("❓ Ask", perms.get("ask", [])),
        ("⛔ Denied", perms.get("deny", [])),
    ]
    lines: list[str] = []
    for header, items in rows:
        if items:
            lines.append(f"{header}: {', '.join(items)}")
    return "\n".join(lines) if lines else "No permission overrides set."


def render_status_card(request: dict) -> str:
    what = request.get("summary", "working")
    elapsed = request.get("elapsed_s", 0)
    return f"⚙️ Active · {what} · {elapsed:.0f}s"


def render_expense_header(pending: dict) -> str:
    amount = pending.get("amount", "?")
    currency = pending.get("currency", "")
    return f"\U0001f4b8 Which project for {amount} {currency}?".rstrip().replace(" ?", "?")
