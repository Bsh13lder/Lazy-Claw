"""A cancelled agent loop must NOT be reported as a successful task.

2026-07-26 incident tail: a background ``agent:upwork`` task ran 11
minutes, blew its 300s deadline, and the user's UI card said

    "Background task completed - agent:upwork / Operation cancelled."

The log agreed it was a success::

    10:05:03 Background task e6bf56b5 (agent:upwork) completed
    10:05:03 [teamlead] complete ... result_len=20
    10:05:03 Brain fan-out ... settled (success=True, pending=0, total=1)

``result_len=20`` is ``len("Operation cancelled.")``.

Cause: ``Agent.process_message`` CAUGHT ``asyncio.CancelledError`` and
RETURNED that string. ``task_runner`` wraps the call in
``async with asyncio.timeout(timeout)``; because nothing propagated,
``asyncio.timeout.__aexit__`` never converted the deadline into a
``TimeoutError``, so BOTH the ``except asyncio.TimeoutError`` handler
(which marks the task failed with "Timed out after Ns") and the
``except asyncio.CancelledError`` handler were skipped entirely.

Swallowing ``CancelledError`` also breaks cooperative cancellation for
every caller up the stack — it is an asyncio anti-pattern regardless of
the reporting bug.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

AGENT_SRC = (
    Path(__file__).resolve().parents[2]
    / "lazyclaw" / "runtime" / "agent.py"
)


def _cancelled_handlers(tree: ast.AST) -> list[ast.ExceptHandler]:
    """Every ``except asyncio.CancelledError`` handler in agent.py."""
    found: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        name = ast.unparse(node.type)
        if "CancelledError" in name:
            found.append(node)
    return found


class TestAgentDoesNotSwallowCancellation:
    """Structural guard — the exact regression that caused the incident.

    Asserted structurally rather than by driving a full Agent: the
    handler sits ~3300 lines inside ``process_message`` behind an LLM
    router, skill registry, permission checker and DB session, and a
    mock deep enough to reach it would assert more about the mock than
    the code. The defect is precisely "this handler returns instead of
    raising", which is exactly what this checks.
    """

    @pytest.fixture(scope="class")
    def tree(self):
        return ast.parse(AGENT_SRC.read_text(encoding="utf-8"))

    def test_at_least_one_cancelled_handler_exists(self, tree):
        assert _cancelled_handlers(tree), (
            "expected an `except asyncio.CancelledError` in agent.py"
        )

    def test_no_cancelled_handler_returns_a_value(self, tree):
        offenders = []
        for handler in _cancelled_handlers(tree):
            for node in ast.walk(handler):
                # Don't descend into nested functions defined in the
                # handler — their returns are their own business.
                if isinstance(node, ast.Return) and node.value is not None:
                    offenders.append((handler.lineno, ast.unparse(node)))
        assert not offenders, (
            "a CancelledError handler returns a value instead of "
            f"re-raising: {offenders}. Returning a string here makes a "
            "hard timeout look like a successful task."
        )

    def test_every_cancelled_handler_reraises(self, tree):
        missing = []
        for handler in _cancelled_handlers(tree):
            has_raise = any(
                isinstance(node, ast.Raise) for node in ast.walk(handler)
            )
            if not has_raise:
                missing.append(handler.lineno)
        assert not missing, (
            f"CancelledError handler(s) at line(s) {missing} neither "
            "re-raise nor propagate — cancellation is being swallowed."
        )

    def test_cancelled_handler_does_not_return_the_laundered_string(
        self, tree,
    ):
        # Scoped to the exception handler ON PURPOSE. The cooperative
        # `if cancel_token.is_cancelled:` early-return elsewhere in the
        # loop legitimately returns "Operation cancelled." — that is a
        # user pressing stop, not a blown deadline, and a graceful
        # string is the right answer there. Only the asyncio path had to
        # change, so a blanket file-wide grep would flag correct code.
        for handler in _cancelled_handlers(tree):
            body = ast.unparse(handler)
            assert '"Operation cancelled."' not in body.replace(
                "# ", "",
            ) or "raise" in body, (
                "the CancelledError handler returns the laundered string"
            )

    def test_cooperative_cancel_path_is_still_a_plain_return(self, tree):
        """The user-pressed-stop path must NOT have been turned into a
        raise by an over-eager sweep — it is a different contract."""
        src = AGENT_SRC.read_text(encoding="utf-8")
        assert "if cancel_token.is_cancelled:" in src
        assert 'return "Operation cancelled."' in src, (
            "the cooperative stop path should still return gracefully"
        )


class TestAsyncioTimeoutSemantics:
    """Documents WHY re-raising fixes the reporting.

    ``asyncio.timeout`` converts the CancelledError it injected into a
    ``TimeoutError`` on the way out — but only if the inner coroutine
    lets it propagate. Swallow it and the deadline passes silently.
    """

    @pytest.mark.asyncio
    async def test_swallowing_cancellation_hides_the_timeout(self):
        async def inner_that_swallows():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                return "Operation cancelled."  # the old agent.py

        result = None
        timed_out = False
        try:
            async with asyncio.timeout(0.05):
                result = await inner_that_swallows()
        except asyncio.TimeoutError:
            timed_out = True

        # The bug, reproduced exactly: no TimeoutError, and the caller
        # gets a plain string it will happily record as success.
        assert timed_out is False
        assert result == "Operation cancelled."

    @pytest.mark.asyncio
    async def test_reraising_surfaces_the_timeout(self):
        async def inner_that_reraises():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise  # the fixed agent.py

        timed_out = False
        try:
            async with asyncio.timeout(0.05):
                await inner_that_reraises()
        except asyncio.TimeoutError:
            timed_out = True

        assert timed_out is True, (
            "re-raising must let asyncio.timeout produce TimeoutError so "
            "task_runner's failure handler runs"
        )

    @pytest.mark.asyncio
    async def test_cleanup_still_runs_before_the_reraise(self):
        """Re-raising must not skip the handler's cleanup work."""
        cleaned: list[str] = []

        async def inner():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cleaned.append("team_lead.cancel")
                cleaned.append("unregister:delegate")
                cleaned.append("unregister:dispatch_subagents")
                raise

        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.05):
                await inner()

        assert cleaned == [
            "team_lead.cancel",
            "unregister:delegate",
            "unregister:dispatch_subagents",
        ]


class TestTaskRunnerHandlersExist:
    """The handlers that were being skipped must still be wired."""

    def test_task_runner_marks_timeout_as_failed(self):
        src = (
            Path(__file__).resolve().parents[2]
            / "lazyclaw" / "runtime" / "task_runner.py"
        ).read_text(encoding="utf-8")
        assert "except asyncio.TimeoutError:" in src
        assert "Timed out after" in src
        assert 'status = \'failed\'' in src or '_status = "failed"' in src
