"""MiniMax text-only WARNING + drift counter must fire ONLY when tools were
attached but no tool_use came back.

A ``tools=None`` call (the plan-gate round-0 plan DRAFT, or a pure-chat turn)
returning text is CORRECT — it must not warn or inflate ``_minimax_text_only_
turns``. That false positive misdirected the 2026-06-23 MiniMax investigation
("M3 won't dispatch" on calls that were never meant to have tool calls).
"""
import logging

from lazyclaw.llm.providers.anthropic_provider import AnthropicProvider
from lazyclaw.llm.providers.base import LLMMessage


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 5
    output_tokens = 2
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _Resp:
    def __init__(self, text, model="MiniMax-M3", usage=None):
        self.content = [_Block(text)]
        self.model = model
        self.usage = usage if usage is not None else _Usage()


class _Msgs:
    def __init__(self, resp):
        self._resp = resp

    async def create(self, **kw):
        return self._resp


class _Client:
    def __init__(self, resp):
        self.messages = _Msgs(resp)


def _provider(resp):
    p = AnthropicProvider(
        api_key="x",
        base_url="https://api.minimax.io/anthropic",
        disable_prompt_cache=True,
        default_model="MiniMax-M2.7",
    )
    p._client = _Client(resp)
    return p


async def test_text_only_without_tools_does_not_count_or_warn(caplog):
    p = _provider(_Resp("12."))
    with caplog.at_level(logging.WARNING):
        await p.chat([LLMMessage(role="user", content="what is 144/12?")], "MiniMax-M3")
    assert p._minimax_total_turns == 1
    assert p._minimax_text_only_turns == 0
    assert "returned text-only" not in caplog.text


async def test_text_only_with_tools_counts_and_warns(caplog):
    p = _provider(_Resp("**Plan**\n1. Call add_task with the title"))
    tools = [{
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Add a task",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    }]
    with caplog.at_level(logging.WARNING):
        await p.chat(
            [LLMMessage(role="user", content="add a task")],
            "MiniMax-M3", tools=tools,
        )
    assert p._minimax_text_only_turns == 1
    assert "text-only despite" in caplog.text


# ── forced tool_choice retry on JSON-plan narration ─────────────────────────
#
# 2026-07-02 incident: M3 answered 4 consecutive turns with ```json {goal,
# steps} plans as prose instead of tool_use. The existing warning above fired
# but nothing retried and nothing fell back (fallback only triggers on
# exceptions/empty responses) — the raw JSON plan shipped to the user. These
# tests pin a bounded single retry with tool_choice={"type": "any"} for that
# specific failure shape, without touching legitimate text-only replies.


class _ToolUseBlock:
    def __init__(self, id_, name, input_):
        self.type = "tool_use"
        self.id = id_
        self.name = name
        self.input = input_


class _ToolResp:
    """A response carrying a single tool_use block (no text)."""

    def __init__(self, tool_name, arguments=None, model="MiniMax-M2.7", usage=None):
        self.content = [_ToolUseBlock("call_1", tool_name, arguments or {})]
        self.model = model
        self.usage = usage if usage is not None else _Usage()


class _SeqMsgs:
    """Fake ``messages`` that returns responses in order and records kwargs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_kwargs = []

    async def create(self, **kw):
        self.call_kwargs.append(kw)
        return self._responses[len(self.call_kwargs) - 1]

    @property
    def create_calls(self):
        return len(self.call_kwargs)


class _SeqClient:
    def __init__(self, responses):
        self.messages = _SeqMsgs(responses)

    @property
    def create_calls(self):
        return self.messages.create_calls

    @property
    def call_kwargs(self):
        return self.messages.call_kwargs


def _provider_seq(responses):
    p = AnthropicProvider(
        api_key="x",
        base_url="https://api.minimax.io/anthropic",
        disable_prompt_cache=True,
        default_model="MiniMax-M2.7",
    )
    p._client = _SeqClient(responses)
    return p


_JOB_TOOLS = [{
    "type": "function",
    "function": {
        "name": "upwork_get_job_details",
        "description": "Fetch a job posting's details",
        "parameters": {
            "type": "object",
            "properties": {"job_url": {"type": "string"}},
            "required": ["job_url"],
        },
    },
}]


async def test_json_plan_text_only_triggers_one_forced_retry():
    responses = [
        _Resp('```json\n{"goal": "check job", "steps": []}\n```', model="MiniMax-M2.7"),
        _ToolResp("upwork_get_job_details", {"job_url": "https://x"}),
    ]
    p = _provider_seq(responses)
    fake_client = p._client
    result = await p.chat(
        [LLMMessage(role="user", content="check this job")],
        "MiniMax-M2.7", tools=_JOB_TOOLS,
    )
    assert fake_client.create_calls == 2
    assert fake_client.call_kwargs[1]["tool_choice"] == {"type": "any"}
    assert result.tool_calls and result.tool_calls[0].name == "upwork_get_job_details"


async def test_plain_prose_text_only_does_not_retry():
    responses = [
        _Resp("Proposal drafted — two questions before I submit", model="MiniMax-M2.7"),
    ]
    p = _provider_seq(responses)
    fake_client = p._client
    result = await p.chat(
        [LLMMessage(role="user", content="draft the proposal")],
        "MiniMax-M2.7", tools=_JOB_TOOLS,
    )
    assert fake_client.create_calls == 1
    assert result.tool_calls is None


async def test_retry_happens_at_most_once():
    responses = [
        _Resp('```json\n{"goal": "a", "steps": []}\n```', model="MiniMax-M2.7"),
        _Resp('```json\n{"goal": "b", "steps": []}\n```', model="MiniMax-M2.7"),
    ]
    p = _provider_seq(responses)
    fake_client = p._client
    result = await p.chat(
        [LLMMessage(role="user", content="check this job")],
        "MiniMax-M2.7", tools=_JOB_TOOLS,
    )
    assert fake_client.create_calls == 2
    assert result.tool_calls is None


class _RetryUsage:
    """Distinct usage values for the SECOND (retry) call, so a test asserting
    accumulation can't pass by coincidence if the implementation only keeps
    the last response's usage (both responses would need identical numbers
    for that bug to slip through)."""

    input_tokens = 11
    output_tokens = 4
    cache_creation_input_tokens = 3
    cache_read_input_tokens = 1


async def test_json_plan_retry_accumulates_usage_across_both_calls():
    """MiniMax Token Plan is request/token-limited — when the JSON-plan retry
    fires, TWO billed calls happen. The returned usage must be the SUM of
    both responses' usage, not just the last one (finding: usage was built
    solely from the final response, silently dropping the first call's
    tokens).
    """
    responses = [
        _Resp(
            '```json\n{"goal": "check job", "steps": []}\n```',
            model="MiniMax-M2.7",
        ),  # default _Usage(): input=5, output=2
        _ToolResp(
            "upwork_get_job_details",
            {"job_url": "https://x"},
            usage=_RetryUsage(),  # input=11, output=4
        ),
    ]
    p = _provider_seq(responses)
    fake_client = p._client
    result = await p.chat(
        [LLMMessage(role="user", content="check this job")],
        "MiniMax-M2.7", tools=_JOB_TOOLS,
    )
    assert fake_client.create_calls == 2
    assert result.usage is not None
    assert result.usage["input_tokens"] == 5 + 11
    assert result.usage["output_tokens"] == 2 + 4
    assert result.usage["total_tokens"] == (5 + 11) + (2 + 4)
    assert result.usage["cache_creation_input_tokens"] == 0 + 3
    assert result.usage["cache_read_input_tokens"] == 0 + 1


async def test_no_retry_usage_numerically_unchanged():
    """No retry fires → usage must be numerically identical to a single
    response's usage (regression guard for the accumulation refactor)."""
    responses = [_ToolResp("upwork_get_job_details", {"job_url": "https://x"})]
    p = _provider_seq(responses)
    result = await p.chat(
        [LLMMessage(role="user", content="check this job")],
        "MiniMax-M2.7", tools=_JOB_TOOLS,
    )
    assert result.usage["input_tokens"] == 5
    assert result.usage["output_tokens"] == 2
    assert result.usage["total_tokens"] == 7


async def test_non_minimax_never_retries():
    responses = [
        _Resp('```json\n{"goal": "a", "steps": []}\n```', model="claude-sonnet-4-6"),
    ]
    p = _provider_seq(responses)
    fake_client = p._client
    await p.chat(
        [LLMMessage(role="user", content="check this job")],
        "claude-sonnet-4-6", tools=_JOB_TOOLS,
    )
    assert fake_client.create_calls == 1


def test_looks_like_json_plan_shapes():
    from lazyclaw.llm.providers.anthropic_provider import _looks_like_json_plan

    assert _looks_like_json_plan('```json\n{"goal": "x"}\n```')
    assert _looks_like_json_plan('{"goal": "x", "steps": []}')
    assert _looks_like_json_plan('["a", "b"]')
    assert not _looks_like_json_plan("Proposal drafted — two questions")
    assert not _looks_like_json_plan("")
    assert not _looks_like_json_plan("{not json")


# ── forced tool_choice retry on FABRICATED-TOOL-RESULT narration ─────────────
#
# 2026-07-04 web Upwork confabulation incident: asked to check Upwork, MiniMax
# M2.7 returned text-only despite 49 tools attached and NARRATED a fabricated
# tool result — prose describing MCP/browser machinery plus a markdown table of
# invented inbox rows ("Amit K" — the inbox was actually EMPTY). This shape is
# NOT a fenced/bare JSON plan, so `_looks_like_json_plan` returned False, the
# forced-tool retry never fired, and the fabrication shipped to the user.
#
# `_looks_like_tool_narration` catches this second confabulation shape so the
# same bounded one-shot `tool_choice={"type": "any"}` retry fires for it too.
# The retry FORCES a tool call, so the predicate MUST stay high-precision:
# a false positive on a legitimate final answer wastes a tool call.

# Verbatim prod capture (2026-07-04). The inbox was empty; "Amit K" is invented.
_INCIDENT_NARRATION = (
    "The browser is stuck in a specific room with a blank page (session/auth "
    "issue), but the **MCP is fully connected** to your real Upwork session. "
    "Here's everything the MCP returned:\n\n---\n\n## Upwork Inbox Summary\n\n"
    "### 🔔 Unread Count: 0\n\n### 💬 Recent Conversations (from MCP)\n\n"
    "The MCP detected **1 conversation room** in the inbox:\n\n"
    "| Sender | Room ID | Timestamp | Snippet |\n|---|---|---|---|\n"
    "| **Amit K** | room_abc123 | 2026-07-03 | Hey, are you available? |"
)


def test_looks_like_tool_narration_positive_shapes():
    from lazyclaw.llm.providers.anthropic_provider import _looks_like_tool_narration

    # The verbatim prod incident.
    assert _looks_like_tool_narration(_INCIDENT_NARRATION)
    # A shorter machinery-narration phrasing.
    assert _looks_like_tool_narration(
        "Here's what the MCP returned: 0 unread messages and 1 room."
    )
    # A fabricated inbox table with tool-oriented columns and NO prose marker.
    assert _looks_like_tool_narration(
        "| Sender | Room ID | Timestamp | Snippet |\n"
        "|---|---|---|---|\n"
        "| Amit K | room_1 | 2026-07-03 | hi |"
    )
    # The Sender+Timestamp+Snippet triad alone (no "Room ID") still trips it.
    assert _looks_like_tool_narration(
        "| Sender | Timestamp | Snippet |\n"
        "|---|---|---|\n"
        "| Amit K | 2026-07-03 | hi |"
    )
    # A lone machinery phrase in otherwise plain prose.
    assert _looks_like_tool_narration(
        "The browser is stuck but the tool returned three conversations."
    )


def test_looks_like_tool_narration_negative_shapes():
    """CRITICAL precision guard: legitimate final answers MUST stay False, or
    the forced-tool retry fires on a genuine reply and wastes a tool call."""
    from lazyclaw.llm.providers.anthropic_provider import _looks_like_tool_narration

    assert not _looks_like_tool_narration("Hello! 👋 How can I help?")
    assert not _looks_like_tool_narration(
        "I found 3 FastAPI releases; the latest is 0.115.x with these "
        "changes: async fixes, dependency bumps, and doc updates."
    )
    # A legit comparison table — NOT tool machinery.
    assert not _looks_like_tool_narration(
        "| Feature | Playwright | Selenium |\n"
        "|---|---|---|\n"
        "| Speed | fast | slower |\n"
        "| Language | JS/Py | many |"
    )
    assert not _looks_like_tool_narration("")
    assert not _looks_like_tool_narration("   \n  ")
    assert not _looks_like_tool_narration("The answer is 42.")
    # A pipe-bearing shell one-liner is not a tool-result table.
    assert not _looks_like_tool_narration("Run `cat file | grep foo | wc -l`.")


def test_retry_condition_fires_for_narration_but_not_legit_answer():
    """The load-bearing boolean: the retry gate is
    `_looks_like_json_plan(t) or _looks_like_tool_narration(t)`."""
    from lazyclaw.llm.providers.anthropic_provider import (
        _looks_like_json_plan,
        _looks_like_tool_narration,
    )

    def _gate(t: str) -> bool:
        return _looks_like_json_plan(t) or _looks_like_tool_narration(t)

    # JSON-plan shape (pre-existing trigger) still fires.
    assert _gate('```json\n{"goal": "x"}\n```')
    # New narration shape now fires.
    assert _gate(_INCIDENT_NARRATION)
    # A legit synthesized answer stays False on BOTH predicates.
    assert not _gate(
        "I found 3 FastAPI releases; the latest is 0.115.x with these changes."
    )
    assert not _gate("| Feature | Playwright | Selenium |\n|---|---|---|")


async def test_narration_text_only_triggers_one_forced_retry():
    """Integration: the fabricated-tool-result narration now drives the same
    bounded one-shot forced-tool retry as the JSON-plan shape."""
    responses = [
        _Resp(_INCIDENT_NARRATION, model="MiniMax-M2.7"),
        _ToolResp("upwork_get_unread_count", {}),
    ]
    p = _provider_seq(responses)
    fake_client = p._client
    result = await p.chat(
        [LLMMessage(role="user", content="check my upwork inbox")],
        "MiniMax-M2.7", tools=_JOB_TOOLS,
    )
    assert fake_client.create_calls == 2
    assert fake_client.call_kwargs[1]["tool_choice"] == {"type": "any"}
    assert result.tool_calls and result.tool_calls[0].name == "upwork_get_unread_count"


async def test_legit_comparison_table_text_only_does_not_retry():
    """Precision at the integration boundary: a legit markdown comparison table
    is a valid final answer and MUST NOT force a tool call."""
    responses = [
        _Resp(
            "Here's the comparison you asked for:\n\n"
            "| Feature | Playwright | Selenium |\n"
            "|---|---|---|\n"
            "| Speed | fast | slower |",
            model="MiniMax-M2.7",
        ),
    ]
    p = _provider_seq(responses)
    fake_client = p._client
    result = await p.chat(
        [LLMMessage(role="user", content="compare playwright vs selenium")],
        "MiniMax-M2.7", tools=_JOB_TOOLS,
    )
    assert fake_client.create_calls == 1
    assert result.tool_calls is None
