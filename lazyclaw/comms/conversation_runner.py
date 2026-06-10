"""AI conversation runner — Phase E v1 ("Ask AI" inbox replies).

``start()`` dispatches ONE grounded background agent turn via TaskRunner:

  1. READ the thread with the channel's read tool (grounding contract —
     the channel read comes FIRST, reply is composed from live messages,
     never from memory).
  2. COMPOSE a reply that advances the user's goal, matching the
     conversation's tone.
  3. SEND it with the channel's send tool.
  4. REPORT back exactly what was sent (the user sees the result through
     the normal background-task notification path, which honours the
     telegram | app | both channel preference).

The full multi-turn state machine (hold the conversation over hours until
the goal is achieved, re-engaging on replies) layers on top of this same
entry point later — the route contract ``start(...) -> {"id": ...}`` is
already shaped for it.
"""

from __future__ import annotations

import logging
from typing import Any

from lazyclaw.comms.gateway import _DISPATCH

logger = logging.getLogger(__name__)

_INSTRUCTION_TEMPLATE = """\
[INBOX_AI_REPLY] You are replying ON BEHALF OF the user in their {channel} \
conversation with {contact_label}.

1. FIRST call {read_tool} for contact "{contact}" to fetch the latest \
messages. Ground yourself in what was ACTUALLY written — quote the recent \
messages to yourself before composing. Never invent details.
2. Compose a reply that accomplishes the user's goal: "{goal}"
   - Match the tone and language of the conversation. Keep it natural and \
concise — this is a real {channel} message to a real person.
3. Send it with {send_tool} to "{contact}".
4. Report back with the EXACT reply text you sent plus anything important \
you learned from the thread.

If the send fails or the goal cannot be advanced from the conversation, do \
NOT improvise or retry endlessly — report what happened so the user can \
step in."""


def _resolve_task_runner(explicit: Any | None) -> Any | None:
    """Explicit runner (tests / DI) → module singleton (production) → None."""
    if explicit is not None:
        return explicit
    try:
        from lazyclaw.runtime import task_runner as tr_mod
        return tr_mod._task_runner_instance
    except Exception:
        logger.debug("task runner singleton unavailable", exc_info=True)
        return None


def build_instruction(
    channel: str,
    contact: str,
    goal: str,
    *,
    contact_name: str | None = None,
) -> str:
    """Render the grounded reply instruction for one channel thread."""
    spec = _DISPATCH.get(channel)
    if not spec:
        raise ValueError(f"unsupported channel: {channel}")
    read_tool, send_tool = spec[0], spec[1]
    return _INSTRUCTION_TEMPLATE.format(
        channel=channel,
        contact=contact,
        contact_label=contact_name or contact,
        goal=goal,
        read_tool=read_tool,
        send_tool=send_tool,
    )


async def start(
    config: Any,
    user_id: str,
    *,
    channel: str,
    contact: str,
    goal: str,
    contact_name: str | None = None,
    task_runner: Any | None = None,
) -> dict:
    """Start an AI-driven reply on a channel thread.

    Returns ``{"id": <task_id>, "status": "running"}``. Raises
    :class:`ValueError` for an unsupported channel (route → 400) and
    :class:`RuntimeError` when the agent runtime isn't available
    (route → 503).
    """
    instruction = build_instruction(
        channel, contact, goal, contact_name=contact_name,
    )

    runner = _resolve_task_runner(task_runner)
    if runner is None:
        raise RuntimeError(
            "AI conversation mode needs the agent runtime "
            "(task runner not available)"
        )

    # Raw handles (phone numbers / emails) stay out of the plaintext task
    # name — it surfaces in push titles. The encrypted instruction carries
    # the real handle.
    display = contact_name or f"{channel} contact"
    task_id = await runner.submit(
        user_id,
        instruction,
        name=f"AI reply · {channel} · {display}"[:80],
        source="user",
    )
    logger.info(
        "inbox AI reply dispatched (channel=%s task=%s)", channel, task_id,
    )
    return {"id": task_id, "status": "running"}
