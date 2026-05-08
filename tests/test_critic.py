"""Unit tests for lazyclaw.runtime.critic.

Covers:
  - First-pass success → ship as-is, no rewrite
  - First fail → brain rewrite → second-pass success → ship rewritten
  - Three fails → exhaust loop → ship last draft with footer note
  - Empty critic output → fail open (don't block reply)
  - Malformed JSON → fail open
  - Code-fenced JSON output → still parses
  - build_tool_trace formatting + truncation
  - Rewrite call is text-only (uses ROLE_BRAIN, no tools kwarg)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from lazyclaw.llm.providers.base import LLMMessage, LLMResponse
from lazyclaw.runtime.critic import (
    CriticResult,
    build_tool_trace,
    run_critic,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Fake EcoRouter — records every chat call and returns scripted replies


@dataclass
class _Call:
    messages: list[LLMMessage]
    role: str
    model: str | None
    kwargs: dict


@dataclass
class _FakeEcoRouter:
    """Stand-in for EcoRouter.chat with scripted, ordered responses.

    The first N calls go to the critic (audit), interleaved with brain
    rewrites in between. Each call's role and model are recorded so
    tests can assert routing.
    """

    audit_responses: list[str]
    rewrite_responses: list[str] = field(default_factory=list)
    calls: list[_Call] = field(default_factory=list)

    async def chat(self, messages, user_id, model=None, role="brain", **kwargs):
        self.calls.append(_Call(
            messages=list(messages), role=role, model=model, kwargs=dict(kwargs),
        ))
        # Anything not BRAIN is treated as a critic call by run_critic.
        if role != "brain":
            content = self.audit_responses.pop(0)
        else:
            content = self.rewrite_responses.pop(0)
        return LLMResponse(content=content, model=model or "fake-model", usage=None)


# ── Tests ──────────────────────────────────────────────────────────────


def test_first_pass_ships_as_is():
    """Critic returns pass=true on the first call → original reply unchanged."""
    fake = _FakeEcoRouter(audit_responses=[
        '{"pass": true, "issues": [], "fix_hint": null}',
    ])
    result = _run(run_critic(
        user_message="hi",
        reply="Hello!",
        tool_trace="(no tools)",
        base_messages=[LLMMessage(role="user", content="hi")],
        eco_router=fake,
        user_id="u1",
        max_loops=3,
    ))
    assert result.passed is True
    assert result.final_reply == "Hello!"
    assert result.loops_used == 1
    assert result.rewrite_count == 0
    # Only one critic call, no rewrite call.
    assert len(fake.calls) == 1
    assert fake.calls[0].role != "brain"


def test_fail_then_pass_returns_rewritten_reply():
    """Critic fails → brain rewrites → critic passes second pass."""
    fake = _FakeEcoRouter(
        audit_responses=[
            '{"pass": false, "issues": ["Reply ignored tool failure"], '
            '"fix_hint": "Acknowledge the HTTP 500 from web_search."}',
            '{"pass": true, "issues": [], "fix_hint": null}',
        ],
        rewrite_responses=[
            "Apologies — the search failed with HTTP 500. I couldn't fetch the data.",
        ],
    )
    result = _run(run_critic(
        user_message="search for X",
        reply="Done! Here are the results: [...].",
        tool_trace="- web_search → → FAILED: HTTP 500",
        base_messages=[LLMMessage(role="user", content="search for X")],
        eco_router=fake,
        user_id="u1",
        max_loops=3,
    ))
    assert result.passed is True
    assert "HTTP 500" in result.final_reply
    assert "Apologies" in result.final_reply
    assert result.loops_used == 2
    assert result.rewrite_count == 1
    # Three calls total: critic, rewrite, critic
    assert len(fake.calls) == 3
    assert fake.calls[0].role != "brain"   # audit
    assert fake.calls[1].role == "brain"   # rewrite
    assert fake.calls[2].role != "brain"   # re-audit


def test_three_fails_exhaust_loop_with_footer():
    """All three audits fail → ship last draft + critic footer."""
    audit_fail = (
        '{"pass": false, "issues": ["Hallucinated price"], '
        '"fix_hint": "Remove the made-up $50 figure."}'
    )
    fake = _FakeEcoRouter(
        audit_responses=[audit_fail, audit_fail, audit_fail],
        rewrite_responses=[
            "Updated draft 1 — still mentions $50.",
            "Updated draft 2 — still mentions $50.",
        ],
    )
    result = _run(run_critic(
        user_message="what's the price?",
        reply="It costs $50.",
        tool_trace="- web_search → no price found",
        base_messages=[LLMMessage(role="user", content="what's the price?")],
        eco_router=fake,
        user_id="u1",
        max_loops=3,
    ))
    assert result.passed is False
    assert result.loops_used == 3
    assert result.rewrite_count == 2
    # Footer must mention the flagged issue.
    assert "_Note: critic still flagged:" in result.final_reply
    assert "Hallucinated price" in result.final_reply
    # 5 calls total: 3 audits + 2 rewrites.
    assert len(fake.calls) == 5


def test_empty_critic_output_fails_open():
    """If critic returns "", run_critic must not block the reply."""
    fake = _FakeEcoRouter(audit_responses=[""])
    result = _run(run_critic(
        user_message="hi",
        reply="Hello!",
        tool_trace="(no tools)",
        base_messages=[LLMMessage(role="user", content="hi")],
        eco_router=fake,
        user_id="u1",
        max_loops=3,
    ))
    assert result.passed is True
    assert result.final_reply == "Hello!"
    assert result.loops_used == 1


def test_malformed_json_fails_open():
    """Garbage critic output must not block the reply."""
    fake = _FakeEcoRouter(audit_responses=[
        "I think the reply is okay, no issues. (no JSON here)",
    ])
    result = _run(run_critic(
        user_message="hi",
        reply="Hello!",
        tool_trace="(no tools)",
        base_messages=[LLMMessage(role="user", content="hi")],
        eco_router=fake,
        user_id="u1",
        max_loops=3,
    ))
    assert result.passed is True
    assert result.final_reply == "Hello!"


def test_code_fenced_json_still_parses():
    """Some models wrap JSON in ```json … ``` — must still parse."""
    fake = _FakeEcoRouter(audit_responses=[
        "```json\n{\"pass\": true, \"issues\": [], \"fix_hint\": null}\n```",
    ])
    result = _run(run_critic(
        user_message="hi",
        reply="Hello!",
        tool_trace="(no tools)",
        base_messages=[LLMMessage(role="user", content="hi")],
        eco_router=fake,
        user_id="u1",
        max_loops=3,
    ))
    assert result.passed is True
    assert result.final_reply == "Hello!"


def test_critic_uses_picked_model_when_provided():
    """When model_id is set, that model id must reach eco_router.chat."""
    fake = _FakeEcoRouter(audit_responses=[
        '{"pass": true, "issues": [], "fix_hint": null}',
    ])
    _run(run_critic(
        user_message="hi",
        reply="Hello!",
        tool_trace="(no tools)",
        base_messages=[LLMMessage(role="user", content="hi")],
        eco_router=fake,
        user_id="u1",
        model_id="claude-cli",
        max_loops=3,
    ))
    assert fake.calls[0].model == "claude-cli"
    # Role is anything other than BRAIN/WORKER so the picked-model path
    # bypasses routing in the real EcoRouter.
    assert fake.calls[0].role not in ("brain", "worker")


def test_critic_falls_back_to_worker_role_when_no_model():
    """No model_id → audit goes through ROLE_WORKER."""
    fake = _FakeEcoRouter(audit_responses=[
        '{"pass": true, "issues": [], "fix_hint": null}',
    ])
    _run(run_critic(
        user_message="hi",
        reply="Hello!",
        tool_trace="(no tools)",
        base_messages=[LLMMessage(role="user", content="hi")],
        eco_router=fake,
        user_id="u1",
        max_loops=3,
    ))
    assert fake.calls[0].role == "worker"
    assert fake.calls[0].model is None


def test_rewrite_call_is_text_only():
    """The rewrite call must NOT pass tools — text-only reply expected."""
    fake = _FakeEcoRouter(
        audit_responses=[
            '{"pass": false, "issues": ["x"], "fix_hint": "fix it"}',
            '{"pass": true, "issues": [], "fix_hint": null}',
        ],
        rewrite_responses=["fixed reply"],
    )
    _run(run_critic(
        user_message="hi",
        reply="bad reply",
        tool_trace="(no tools)",
        base_messages=[LLMMessage(role="user", content="hi")],
        eco_router=fake,
        user_id="u1",
        max_loops=3,
    ))
    rewrite_call = [c for c in fake.calls if c.role == "brain"][0]
    assert "tools" not in rewrite_call.kwargs


def test_rewrite_message_contains_critic_feedback():
    """The rewrite directive must include the fix_hint as [CRITIC FEEDBACK: ...]."""
    fake = _FakeEcoRouter(
        audit_responses=[
            '{"pass": false, "issues": ["price not in trace"], '
            '"fix_hint": "Drop the $50 figure."}',
            '{"pass": true, "issues": [], "fix_hint": null}',
        ],
        rewrite_responses=["Sorry — I don't have a confirmed price."],
    )
    _run(run_critic(
        user_message="price?",
        reply="$50",
        tool_trace="- web_search → no price found",
        base_messages=[LLMMessage(role="user", content="price?")],
        eco_router=fake,
        user_id="u1",
        max_loops=3,
    ))
    rewrite_call = [c for c in fake.calls if c.role == "brain"][0]
    last_user = rewrite_call.messages[-1]
    assert last_user.role == "user"
    assert "[CRITIC FEEDBACK:" in last_user.content
    assert "Drop the $50 figure" in last_user.content


def test_build_tool_trace_formats_pairs():
    trace = build_tool_trace(
        ["web_search", "browser"],
        ["10 results found", "page loaded — title: Example"],
    )
    assert "web_search" in trace
    assert "browser" in trace
    assert "10 results found" in trace


def test_build_tool_trace_handles_no_tools():
    trace = build_tool_trace([], [])
    assert "no tools" in trace.lower()


def test_build_tool_trace_truncates_huge_results():
    """A 10KB result must not produce a 10KB trace line."""
    huge = "X" * 10000
    trace = build_tool_trace(["x_tool"], [huge])
    assert len(trace) < 6500   # _MAX_TRACE_CHARS + small overhead
    assert "truncated" in trace
