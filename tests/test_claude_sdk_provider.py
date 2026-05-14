"""Unit tests for the Claude Agent SDK provider.

Covers the adapter surface — message serialization, tool reverse-mapping,
env stripping, ClaudeAgentOptions construction. Network/SDK behavior is
mocked; we only assert the wiring our provider owns.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lazyclaw.llm.providers.base import LLMMessage, ToolCall
from lazyclaw.llm.providers.claude_sdk_provider import (
    ClaudeSDKProvider,
    SDKUnavailable,
    _build_tool_wrappers,
    _dedup_tool_calls,
    _serialize_messages,
    _shorten_tool_name,
)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


class TestShortenToolName:
    def test_strips_mcp_uuid_prefix(self) -> None:
        full = "mcp_c2d0f293-ccf7-4987-a4dd-7edadc97261f_instagram_read_profile"
        assert _shorten_tool_name(full) == "instagram_read_profile"

    def test_plain_name_unchanged(self) -> None:
        assert _shorten_tool_name("save_memory") == "save_memory"

    def test_non_uuid_mcp_prefix_unchanged(self) -> None:
        # Not a real UUID — shouldn't match the pattern, leave alone
        assert _shorten_tool_name("mcp_not_a_uuid_tool") == "mcp_not_a_uuid_tool"


class TestSerializeMessages:
    def test_system_user_assistant_roles(self) -> None:
        msgs = [
            LLMMessage(role="system", content="You are helpful."),
            LLMMessage(role="user", content="Hello"),
            LLMMessage(role="assistant", content="Hi there"),
        ]
        out = _serialize_messages(msgs)
        assert "[System Context]" in out
        assert "[User]" in out
        assert "[Assistant]" in out
        assert "Hello" in out
        assert "Hi there" in out

    def test_tool_role_emits_labeled_result(self) -> None:
        msgs = [
            LLMMessage(
                role="tool", content="The answer is 42.", tool_call_id="call_abc",
            ),
        ]
        out = _serialize_messages(msgs)
        assert "[Tool Result: call_abc]" in out
        assert "The answer is 42." in out

    def test_assistant_with_tool_calls(self) -> None:
        msgs = [
            LLMMessage(
                role="assistant",
                content="Calling the tool...",
                tool_calls=[
                    ToolCall(id="t1", name="search", arguments={"q": "python"}),
                ],
            ),
        ]
        out = _serialize_messages(msgs)
        assert "[Assistant]" in out
        assert "Calling the tool..." in out
        assert "search" in out


class TestBuildToolWrappers:
    def test_empty_input_returns_empty(self) -> None:
        tools, name_map = _build_tool_wrappers([])
        assert tools == []
        assert name_map == {}

    def test_plain_tool_no_reverse_map_entry(self) -> None:
        spec = [{
            "type": "function",
            "function": {
                "name": "save_memory",
                "description": "Save",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        tools, name_map = _build_tool_wrappers(spec)
        assert len(tools) == 1
        # Plain name stays plain → no map entry
        assert "save_memory" not in name_map

    def test_uuid_prefixed_tool_creates_reverse_map(self) -> None:
        full = "mcp_c2d0f293-ccf7-4987-a4dd-7edadc97261f_instagram_read_profile"
        spec = [{
            "type": "function",
            "function": {
                "name": full,
                "description": "Read profile",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        tools, name_map = _build_tool_wrappers(spec)
        assert len(tools) == 1
        # SDK-registered name is shortened; map points short→full
        assert name_map["instagram_read_profile"] == full

    def test_tool_with_no_name_skipped(self) -> None:
        spec = [
            {"type": "function", "function": {"description": "no name"}},
            {"type": "function", "function": {"name": "real_tool", "description": "ok"}},
        ]
        tools, _ = _build_tool_wrappers(spec)
        assert len(tools) == 1


# ---------------------------------------------------------------------------
# provider adapter — chat() unmapping + env stripping
# ---------------------------------------------------------------------------


class TestUnmapToolName:
    def test_strips_sdk_mcp_prefix(self) -> None:
        p = ClaudeSDKProvider(model="sonnet")
        p._tool_name_map = {}
        # SDK prefix only
        assert (
            p._unmap_tool_name("mcp__lazyclaw__save_memory") == "save_memory"
        )

    def test_reverse_maps_short_to_uuid_full(self) -> None:
        p = ClaudeSDKProvider(model="sonnet")
        full = "mcp_c2d0f293-ccf7-4987-a4dd-7edadc97261f_upwork_search"
        p._tool_name_map = {"upwork_search": full}
        assert (
            p._unmap_tool_name("mcp__lazyclaw__upwork_search") == full
        )

    def test_unknown_short_name_falls_through(self) -> None:
        p = ClaudeSDKProvider(model="sonnet")
        p._tool_name_map = {}
        # Foreign name (no prefix, no map hit) — return as-is
        assert p._unmap_tool_name("some_other_name") == "some_other_name"


class TestEnvHandling:
    """The SDK MERGES our env dict on top of os.environ. So we must
    EXPLICITLY set ANTHROPIC_API_KEY="" (and friends) — not just omit
    them — to overwrite the inherited value with an empty string."""

    def test_options_set_anthropic_keys_to_empty_string(self) -> None:
        p = ClaudeSDKProvider(model="sonnet")

        # Mock create_sdk_mcp_server so we don't import the SDK during
        # the test (avoids touching the real binary).
        mock_server_factory = MagicMock(return_value=MagicMock())
        with patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "sk-leaked", "OTHER": "x"}, clear=False
        ):
            options = p._build_options(tools_spec=[], create_sdk_mcp_server=mock_server_factory)

        assert options.env["ANTHROPIC_API_KEY"] == ""
        assert options.env["ANTHROPIC_AUTH_TOKEN"] == ""
        assert options.env["ANTHROPIC_BASE_URL"] == ""

    def test_strict_mcp_config_enabled(self) -> None:
        """Without strict_mcp_config=True the SDK inherits the host
        user's global MCP config, leaking MCPs that lazyclaw's runtime
        doesn't know about."""
        p = ClaudeSDKProvider(model="sonnet")
        options = p._build_options(
            tools_spec=[], create_sdk_mcp_server=MagicMock(return_value=MagicMock()),
        )
        assert options.strict_mcp_config is True

    def test_disallowed_built_ins_includes_toolsearch(self) -> None:
        """Newer Claude Code versions auto-fire ToolSearch before MCP
        tools — must be in the deny-list."""
        p = ClaudeSDKProvider(model="sonnet")
        options = p._build_options(
            tools_spec=[], create_sdk_mcp_server=MagicMock(return_value=MagicMock()),
        )
        assert "ToolSearch" in options.disallowed_tools
        assert "Bash" in options.disallowed_tools
        assert "WebSearch" in options.disallowed_tools

    def test_permission_mode_bypass(self) -> None:
        """lazyclaw owns the permission system. SDK must not prompt."""
        p = ClaudeSDKProvider(model="sonnet")
        options = p._build_options(
            tools_spec=[], create_sdk_mcp_server=MagicMock(return_value=MagicMock()),
        )
        assert options.permission_mode == "bypassPermissions"


class TestUsageNormalization:
    def test_zero_cost_with_subscription_diagnostic(self) -> None:
        u = ClaudeSDKProvider._normalize_usage(
            {"input_tokens": 100, "output_tokens": 50}, api_equiv_cost=0.42,
        )
        assert u["cost_usd"] == 0.0
        assert u["cost_usd_subscription"] == 0.42
        assert u["prompt_tokens"] == 100
        assert u["completion_tokens"] == 50
        assert u["total_tokens"] == 150
        assert u["provider"] == "claude_sdk"

    def test_cache_token_passthrough(self) -> None:
        u = ClaudeSDKProvider._normalize_usage(
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 1234,
                "cache_creation_input_tokens": 567,
            },
            api_equiv_cost=0.1,
        )
        assert u["cache_read_tokens"] == 1234
        assert u["cache_creation_tokens"] == 567

    def test_handles_none_usage(self) -> None:
        u = ClaudeSDKProvider._normalize_usage(None, api_equiv_cost=0.0)
        assert u["prompt_tokens"] == 0
        assert u["completion_tokens"] == 0
        assert u["provider"] == "claude_sdk"


class TestDedupToolCalls:
    """Sonnet under load occasionally emits the same (name, args) tool call
    multiple times. Observed 13× google_run_task with identical args in one
    batch on 2026-05-14. The CLI provider's --json-schema suppressed this;
    SDK path must dedup explicitly."""

    def test_empty_or_single_returns_unchanged(self) -> None:
        assert _dedup_tool_calls([]) == []
        single = [ToolCall(id="a", name="t", arguments={})]
        assert _dedup_tool_calls(single) == single

    def test_drops_duplicates_keeps_first(self) -> None:
        tc1 = ToolCall(id="a", name="search", arguments={"q": "python"})
        tc2 = ToolCall(id="b", name="search", arguments={"q": "python"})
        tc3 = ToolCall(id="c", name="search", arguments={"q": "python"})
        out = _dedup_tool_calls([tc1, tc2, tc3])
        assert len(out) == 1
        assert out[0].id == "a"  # first-wins

    def test_same_tool_different_args_both_survive(self) -> None:
        """Legitimate parallel calls — must NOT collapse."""
        tc1 = ToolCall(id="a", name="search", arguments={"q": "python"})
        tc2 = ToolCall(id="b", name="search", arguments={"q": "rust"})
        out = _dedup_tool_calls([tc1, tc2])
        assert len(out) == 2

    def test_args_key_is_order_insensitive(self) -> None:
        """Two dicts with same content in different declaration order
        are duplicates — JSON dump with sort_keys handles this."""
        tc1 = ToolCall(id="a", name="f", arguments={"x": 1, "y": 2})
        tc2 = ToolCall(id="b", name="f", arguments={"y": 2, "x": 1})
        out = _dedup_tool_calls([tc1, tc2])
        assert len(out) == 1

    def test_carpet_bomb_pattern(self) -> None:
        """Recreates the 2026-05-14 incident: 13 identical calls."""
        tcs = [
            ToolCall(id=f"t{i}", name="google_run_task", arguments={"task": "x"})
            for i in range(13)
        ]
        out = _dedup_tool_calls(tcs)
        assert len(out) == 1
        assert out[0].name == "google_run_task"

    def test_preserves_intra_batch_order(self) -> None:
        """Brain's call order matters for downstream dispatch — dedup
        must not reshuffle survivors."""
        tcs = [
            ToolCall(id="1", name="a", arguments={}),
            ToolCall(id="2", name="b", arguments={}),
            ToolCall(id="3", name="a", arguments={}),  # dup
            ToolCall(id="4", name="c", arguments={}),
        ]
        out = _dedup_tool_calls(tcs)
        assert [tc.name for tc in out] == ["a", "b", "c"]

    def test_non_json_serialisable_args_treated_as_unique(self) -> None:
        """Defensive — exotic args (sets, objects) shouldn't crash the
        dedup pass. They become non-equal under repr fallback."""
        class Exotic:
            pass

        tc1 = ToolCall(id="a", name="t", arguments={"obj": Exotic()})
        tc2 = ToolCall(id="b", name="t", arguments={"obj": Exotic()})
        # Two different Exotic instances → different reprs → both survive
        out = _dedup_tool_calls([tc1, tc2])
        assert len(out) == 2


class TestSDKUnavailableRaisedOnMissingBinary:
    @pytest.mark.asyncio
    async def test_no_binary_raises_sdk_unavailable(self) -> None:
        # Patch the lookup BEFORE constructing the provider — the
        # constructor calls _find_claude_binary() to seed self._claude_bin.
        with patch(
            "lazyclaw.llm.providers.claude_sdk_provider._find_claude_binary",
            return_value=None,
        ):
            p = ClaudeSDKProvider(model="sonnet")
            assert p._claude_bin is None
            with pytest.raises(SDKUnavailable, match="binary not found"):
                await p.chat([LLMMessage(role="user", content="hi")], tools=[])
