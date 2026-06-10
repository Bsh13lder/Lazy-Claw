"""Phase E v1 — AI conversation runner for inbox "Ask AI" replies.

start() dispatches ONE grounded background agent turn via TaskRunner.submit:
read the thread → compose toward the goal → send → report back. The
instruction must name the channel's read + send tools (grounding contract:
channel read FIRST) and carry the user's goal verbatim.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from lazyclaw.comms import conversation_runner

pytestmark = pytest.mark.asyncio


def _runner(task_id: str = "task-1") -> MagicMock:
    runner = MagicMock()
    runner.submit = AsyncMock(return_value=task_id)
    return runner


async def test_start_dispatches_background_task():
    runner = _runner("task-42")
    conv = await conversation_runner.start(
        config=MagicMock(), user_id="u1",
        channel="whatsapp", contact="+34600000000",
        goal="ask if Friday works for the call",
        task_runner=runner,
    )
    assert conv["id"] == "task-42"
    assert conv["status"] == "running"
    runner.submit.assert_awaited_once()


async def test_instruction_contains_goal_tools_and_contact():
    runner = _runner()
    await conversation_runner.start(
        config=MagicMock(), user_id="u1",
        channel="whatsapp", contact="+34600000000",
        goal="ask if Friday works",
        task_runner=runner,
    )
    instruction = runner.submit.await_args.args[1]
    assert "ask if Friday works" in instruction
    assert "whatsapp_read" in instruction       # grounding: read FIRST
    assert "whatsapp_send" in instruction
    assert "+34600000000" in instruction


async def test_email_channel_uses_email_tools():
    runner = _runner()
    await conversation_runner.start(
        config=MagicMock(), user_id="u1",
        channel="email", contact="bob@example.com",
        goal="confirm the invoice was received",
        task_runner=runner,
    )
    instruction = runner.submit.await_args.args[1]
    assert "email_search" in instruction
    assert "email_send" in instruction


async def test_unsupported_channel_raises_value_error():
    with pytest.raises(ValueError, match="carrier-pigeon"):
        await conversation_runner.start(
            config=MagicMock(), user_id="u1",
            channel="carrier-pigeon", contact="x",
            goal="g", task_runner=_runner(),
        )


async def test_missing_task_runner_raises_runtime_error(monkeypatch):
    # No explicit runner AND no global singleton → RuntimeError (route → 503).
    import lazyclaw.runtime.task_runner as tr_mod
    monkeypatch.setattr(tr_mod, "_task_runner_instance", None)
    with pytest.raises(RuntimeError, match="task runner"):
        await conversation_runner.start(
            config=MagicMock(), user_id="u1",
            channel="whatsapp", contact="+1", goal="g",
        )


async def test_task_name_prefers_contact_name():
    runner = _runner()
    await conversation_runner.start(
        config=MagicMock(), user_id="u1",
        channel="whatsapp", contact="+34600000000",
        goal="g", contact_name="Maria",
        task_runner=runner,
    )
    name = runner.submit.await_args.kwargs.get("name", "")
    assert "Maria" in name
    assert "+34600000000" not in name  # keep raw handles out of push titles


async def test_instruction_forbids_improvising_on_failure():
    runner = _runner()
    await conversation_runner.start(
        config=MagicMock(), user_id="u1",
        channel="instagram", contact="someuser",
        goal="g", task_runner=runner,
    )
    instruction = runner.submit.await_args.args[1]
    # failure contract: report back instead of improvising
    assert "report" in instruction.lower()
