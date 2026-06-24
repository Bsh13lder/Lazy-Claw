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
    def __init__(self, text, model="MiniMax-M3"):
        self.content = [_Block(text)]
        self.model = model
        self.usage = _Usage()


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
