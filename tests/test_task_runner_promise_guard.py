"""RC3 — a background worker must never store a bare dispatch-promise as
its "✅ Background task completed" result.

2026-05-29 incident: an AUTO-PROMOTE'd background worker called
`dispatch_subagents` and then replied "two readers are scanning WhatsApp
and Email in parallel. I'll have your summary in a few seconds." with
tool_calls=0. That 126-char promise was stored verbatim as the task
result and rendered as "✅ Background task completed", even though the
worker produced no synthesized answer and its subagents stranded.

`_looks_like_stranded_dispatch_promise` is the tight guard: it fires ONLY
when the worker actually dispatched subagents AND its final reply is a
short action-claim promise (not a real synthesis). It must NOT fire on a
legitimate short result that merely ends with a courtesy line.
"""

from __future__ import annotations

from lazyclaw.runtime.task_runner import _looks_like_stranded_dispatch_promise


_PROMISE = (
    "two readers are scanning WhatsApp and Email in parallel. "
    "I'll have your summary in a few seconds."
)


class TestStrandedPromiseGuard:
    def test_fires_on_dispatch_promise(self) -> None:
        assert _looks_like_stranded_dispatch_promise(
            _PROMISE, ["email_read", "dispatch_subagents"]
        )

    def test_fires_with_mcp_prefixed_dispatch_tool(self) -> None:
        assert _looks_like_stranded_dispatch_promise(
            "I'll fold the results into my next reply shortly.",
            ["search_tools", "dispatch_subagents"],
        )

    def test_no_fire_when_no_subagents_dispatched(self) -> None:
        # Same promise text, but the worker never dispatched — this is the
        # action-claim class handled elsewhere, NOT a stranded fan-out.
        assert not _looks_like_stranded_dispatch_promise(
            _PROMISE, ["email_read"]
        )

    def test_no_fire_on_real_short_result(self) -> None:
        # A real answer that happens to end with a courtesy promise must be
        # preserved — rewriting it would destroy the actual data.
        assert not _looks_like_stranded_dispatch_promise(
            "Bitcoin is $61,240 right now. I'll keep you posted on big moves.",
            ["dispatch_subagents", "web_search"],
        )

    def test_no_fire_on_long_synthesis(self) -> None:
        long_real = (
            "Here is the consolidated summary of your channels.\n\n"
            "WhatsApp: 3 unread — Mom (lunch?), Bank (statement), "
            "Alex (project ping).\nEmail: 5 unread — 2 newsletters, "
            "1 invoice from Acme due Friday, 1 calendar invite, 1 receipt.\n"
            "Nothing urgent except the Acme invoice. " * 4
        )
        assert not _looks_like_stranded_dispatch_promise(
            long_real, ["dispatch_subagents"]
        )

    def test_no_fire_on_empty(self) -> None:
        # Empty results are handled by the dedicated empty-reply fallback.
        assert not _looks_like_stranded_dispatch_promise("", ["dispatch_subagents"])
        assert not _looks_like_stranded_dispatch_promise("   ", ["dispatch_subagents"])

    def test_tools_used_none_safe(self) -> None:
        assert not _looks_like_stranded_dispatch_promise(_PROMISE, None)
