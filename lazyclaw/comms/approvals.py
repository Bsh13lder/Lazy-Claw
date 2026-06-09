"""First-message approval for autonomous conversations.

Approval id format: ``appr-<conversation_id>``. The durable signal is the
conversation_tasks.status; the in-process waiter is a fast-path convenience for
when the resolving call happens in the same process (mobile endpoint / Telegram cb)."""
from __future__ import annotations

from typing import Awaitable, Callable

from lazyclaw.notifications.dispatch import deliver

_WAITERS: dict[str, Callable[[bool], Awaitable[None]]] = {}


def register_waiter(approval_id: str, cb: Callable[[bool], Awaitable[None]]) -> None:
    _WAITERS[approval_id] = cb


async def request_first_message_approval(config, user_id: str, conv: dict, draft: str) -> str:
    approval_id = f"appr-{conv['id']}"
    keyboard = [[
        {"text": "✅ Send it", "callback_data": f"convok:{conv['id']}"},
        {"text": "✋ Cancel", "callback_data": f"convno:{conv['id']}"},
    ]]
    await deliver(
        config, user_id,
        title=f"Approve message to {conv['contact_handle']}",
        body=f"I want to send on {conv['channel']}:\n\n\"{draft}\"\n\nSend it?",
        kind="conversation_approval", inline_keyboard=keyboard,
        thread_ref={"channel": conv["channel"], "contact": conv["contact_handle"]})
    return approval_id


async def resolve(approval_id: str, approved: bool) -> None:
    cb = _WAITERS.pop(approval_id, None)
    if cb is not None:
        await cb(approved)
