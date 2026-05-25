"""Unit tests for the F1 confabulation-retry draft pop helper.

Pins the surgical fix for the 2026-05-22 10:09 self-perpetuating
hallucination: the F1 confabulation retry previously appended the
brain's poisoned draft to ``messages`` BEFORE injecting the raw tool
output, so the retried brain saw its own confabulation as prior
context and reproduced the same hallucinated quotes
(3 unverified → retry → 3 unverified again).

``_pop_confabulated_draft_for_retry`` removes the trailing assistant
draft so the retry's view becomes:

    tool_result → [RAW DATA injection] → brain re-rolls cleanly

These tests pin only the pop helper's contract — defensively safe on
non-assistant tails, removes a true assistant draft otherwise.
"""

from __future__ import annotations

from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.runtime.agent import _pop_confabulated_draft_for_retry


def test_f1_retry_pops_confabulated_assistant_message() -> None:
    """When the last message is an assistant draft, pop it.

    Simulates the in-flight state right before the F1 confabulation
    backstop fires its raw-data injection.
    """
    confabulated = LLMMessage(
        role="assistant",
        content=(
            "> James (10:37 PM): We need a bot for 6 cities\n"
            "James wants an auto-accept bot."
        ),
    )
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content="soul"),
        LLMMessage(role="user", content="check my upwork last conversation"),
        LLMMessage(
            role="tool",
            content='{"messages":[{"sender":"James","content":"real"}]}',
            tool_call_id="call_1",
        ),
        confabulated,
    ]

    popped = _pop_confabulated_draft_for_retry(messages)

    assert popped is confabulated
    assert len(messages) == 3
    assert messages[-1].role == "tool"
    # The poisoned draft must NOT remain anywhere in the context the
    # brain sees on retry.
    assert all(m is not confabulated for m in messages)


def test_f1_retry_does_not_pop_non_assistant_message() -> None:
    """Defensive: never pop a user/tool/system tail.

    If the call site ever fires when the last message isn't an
    assistant draft (off-by-one bug, refactor, race), refuse to mutate
    so we don't strip a fresh tool result or user turn.
    """
    user_tail = LLMMessage(role="user", content="user-tail")
    tool_tail = LLMMessage(
        role="tool", content="result", tool_call_id="call_x",
    )
    system_tail = LLMMessage(role="system", content="injection")

    for tail in (user_tail, tool_tail, system_tail):
        messages: list[LLMMessage] = [
            LLMMessage(role="user", content="hi"),
            tail,
        ]
        before = list(messages)
        popped = _pop_confabulated_draft_for_retry(messages)

        assert popped is None
        assert messages == before, (
            f"messages mutated on {tail.role!r} tail — "
            "must be a no-op for non-assistant tails"
        )


def test_f1_retry_pop_on_empty_messages_is_safe() -> None:
    """No tail at all — return None, never raise."""
    messages: list[LLMMessage] = []
    popped = _pop_confabulated_draft_for_retry(messages)
    assert popped is None
    assert messages == []
