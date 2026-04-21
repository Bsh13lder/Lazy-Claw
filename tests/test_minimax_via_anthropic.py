"""MiniMax is served by AnthropicProvider pointed at MiniMax's Anthropic-compat endpoint.

These tests pin the wiring so a future refactor can't silently resurrect the
OpenAI-compat path (which carried four bugs — see plan file).
"""

from __future__ import annotations

from lazyclaw.config import Config
from lazyclaw.llm.providers.anthropic_provider import AnthropicProvider
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.llm.router import LLMRouter


def test_anthropic_provider_accepts_base_url_and_cache_toggle() -> None:
    p = AnthropicProvider(
        api_key="test-key",
        base_url="https://api.minimax.io/anthropic",
        disable_prompt_cache=True,
        default_model="MiniMax-M2.7",
    )
    assert p._disable_prompt_cache is True
    assert p._default_model == "MiniMax-M2.7"


def test_plain_system_and_tools_has_no_cache_control() -> None:
    system, tools = AnthropicProvider._plain_system_and_tools(
        ["You are helpful.", "Be concise."],
        [{"name": "foo", "description": "", "input_schema": {}}],
    )
    assert isinstance(system, str)
    assert "You are helpful." in system
    assert "Be concise." in system
    assert tools is not None
    assert all("cache_control" not in t for t in tools)


def test_plain_system_returns_none_when_no_system_parts() -> None:
    system, tools = AnthropicProvider._plain_system_and_tools([], None)
    assert system is None
    assert tools is None


def test_with_cache_still_applies_breakpoints_for_real_anthropic() -> None:
    system, tools = AnthropicProvider._with_cache(
        ["sys"],
        [{"name": "a"}, {"name": "b"}],
    )
    assert system[-1]["cache_control"] == {"type": "ephemeral"}
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tools[0]


def test_router_maps_minimax_model_to_anthropic_provider() -> None:
    cfg = Config(
        minimax_api_key="mini-key",
        minimax_base_url="https://api.minimax.io/anthropic",
    )
    router = LLMRouter(cfg)
    provider = router._create_provider("minimax", "mini-key")
    assert isinstance(provider, AnthropicProvider)
    assert provider._disable_prompt_cache is True
    assert provider._default_model == "MiniMax-M2.7"


def test_router_infer_provider_name_for_minimax_models() -> None:
    cfg = Config()
    router = LLMRouter(cfg)
    assert router._infer_provider_name("MiniMax-M2.7") == "minimax"
    assert router._infer_provider_name("minimax-m2.5") == "minimax"


def test_config_default_base_url_points_to_anthropic_endpoint() -> None:
    cfg = Config()
    assert cfg.minimax_base_url.endswith("/anthropic")


def test_serialize_messages_drops_system_role() -> None:
    # AnthropicProvider lifts system content to a top-level param; the
    # messages array must not carry role=system. This is what makes the
    # MiniMax path correct by construction.
    msgs = [
        LLMMessage(role="system", content="You are n8n workflow generator."),
        LLMMessage(role="user", content="Create a workflow."),
    ]
    serialized = AnthropicProvider._serialize_messages(msgs)
    assert all(m["role"] != "system" for m in serialized)
    assert serialized[0]["role"] == "user"
