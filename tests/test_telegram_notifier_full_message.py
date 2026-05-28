"""Telegram notifier delivers full response, not the 200-char CLI preview.

Regression test for 2026-05-28 bug: Task Guardian (cron) pushes were
cut off mid-sentence — e.g. ``🔴 **Build we`` — because the notifier
reads ``WorkSummary.result_preview`` which is hard-capped at 200 chars
in ``summary.build_work_summary``. Fix added ``WorkSummary.result_full``
and routed the notifier through it; also hardened ``_strip_markdown`` so
any future mid-stream truncation does not leak orphan ``**`` tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from lazyclaw.notifications.telegram_notifier import (
    TelegramNotifier,
    _strip_markdown,
)
from lazyclaw.runtime.callbacks import AgentEvent
from lazyclaw.runtime.events import WorkSummary
from lazyclaw.runtime.summary import build_work_summary


# ── _strip_markdown — orphan-tolerant ────────────────────────────────


@pytest.mark.unit
def test_strip_markdown_closes_paired_bold() -> None:
    assert _strip_markdown("**hello** world") == "hello world"


@pytest.mark.unit
def test_strip_markdown_collapses_orphan_double_star() -> None:
    """Truncation leftovers like `**Build we` must not leak to chat."""
    assert _strip_markdown("Build we **dangling here") == "Build we dangling here"


@pytest.mark.unit
def test_strip_markdown_handles_multiline_bold() -> None:
    """`**...**` spanning a newline must still strip cleanly (DOTALL)."""
    text = "intro **line one\nline two** outro"
    assert _strip_markdown(text) == "intro line one\nline two outro"


@pytest.mark.unit
def test_strip_markdown_keeps_plain_text_intact() -> None:
    assert _strip_markdown("plain text no marks") == "plain text no marks"


# ── WorkSummary carries the full response ────────────────────────────


@pytest.mark.unit
def test_build_work_summary_populates_result_full() -> None:
    long_response = "x" * 5_000
    summary = build_work_summary(
        start_time=0.0,
        llm_calls=1,
        tools_used=[],
        specialists=[],
        total_tokens=0,
        user_message="hi",
        response=long_response,
    )
    assert summary.result_preview == long_response[:200]
    assert summary.result_full == long_response


@pytest.mark.unit
def test_worksummary_result_full_defaults_empty() -> None:
    """Back-compat: pickles/callers without result_full still construct."""
    summary = WorkSummary(
        duration_ms=0,
        llm_calls=0,
        tools_used=(),
        specialists_used=(),
        total_tokens=0,
        mode="direct",
        task_description="",
        result_preview="short",
    )
    assert summary.result_full == ""


# ── Notifier path — reads full text, not 200-char preview ────────────


@dataclass
class _FakeBot:
    sent: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.sent = []

    async def send_message(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)


def _patched_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass exponential backoff & real telegram client for the test.

    The notifier resolves the helper lazily via ``from lazyclaw.channels.telegram
    import _telegram_send_with_retry`` to avoid pulling the python-telegram-bot
    package at import time. In the test env that dependency is absent, so we
    inject a stub module in ``sys.modules`` BEFORE the call site executes.
    """
    import sys
    import types

    async def _passthrough(callable_):  # type: ignore[no-untyped-def]
        return await callable_()

    stub = types.ModuleType("lazyclaw.channels.telegram")
    stub._telegram_send_with_retry = _passthrough  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lazyclaw.channels.telegram", stub)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_notifier_sends_full_response_not_just_200_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patched_retry(monkeypatch)
    bot = _FakeBot()
    notifier = TelegramNotifier(
        bot=bot,
        admin_chat_id_fn=lambda: "12345",
        source_is_telegram=False,
        verbose=False,
    )

    long_reply = (
        "Task Guardian — May 28\n\nYou have 4 open tasks:\n"
        + "\n".join(f"🔴 **Task number {i}** — high priority" for i in range(1, 30))
    )
    assert len(long_reply) > 500  # well past the 200-char preview ceiling

    summary = build_work_summary(
        start_time=0.0,
        llm_calls=1,
        tools_used=[],
        specialists=[],
        total_tokens=0,
        user_message="task guardian",
        response=long_reply,
    )

    await notifier.on_event(AgentEvent("work_summary", "", {"summary": summary}))
    await notifier.on_event(AgentEvent("done", "Task complete", {}))

    assert len(bot.sent) == 1
    body = bot.sent[0]["text"]

    # The full last task must be present — i.e. truncation didn't cut it off.
    assert "Task number 29" in body
    # And the closed-pair stripper plus orphan-collapse must have removed all
    # `**` so nothing leaks visually to the user.
    assert "**" not in body
