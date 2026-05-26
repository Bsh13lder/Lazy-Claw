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
    _DISALLOWED_BUILT_INS,
    _serialize_messages,
    _shorten_tool_name,
    _split_leading_system,
)


class TestSplitLeadingSystem:
    """Leading system messages (full SOUL.md + channel/routing context)
    must be routed into the SDK's native system_prompt, not buried in the
    serialized body — so the dispatch CORE LAW + F1 rules get top attention.
    """

    def test_pulls_leading_system_run(self) -> None:
        msgs = [
            LLMMessage(role="system", content="SOUL"),
            LLMMessage(role="system", content="CHANNEL"),
            LLMMessage(role="user", content="hi"),
        ]
        system_text, rest = _split_leading_system(msgs)
        assert system_text == "SOUL\n\nCHANNEL"
        assert [m.role for m in rest] == ["user"]

    def test_no_leading_system(self) -> None:
        msgs = [LLMMessage(role="user", content="hi")]
        system_text, rest = _split_leading_system(msgs)
        assert system_text == ""
        assert len(rest) == 1

    def test_mid_history_system_stays_in_body(self) -> None:
        # A compaction marker mid-conversation is NOT leading — it stays in
        # the serialized body, not the native system_prompt.
        msgs = [
            LLMMessage(role="system", content="SOUL"),
            LLMMessage(role="user", content="q1"),
            LLMMessage(role="system", content="[compacted]"),
            LLMMessage(role="user", content="q2"),
        ]
        system_text, rest = _split_leading_system(msgs)
        assert system_text == "SOUL"
        assert [m.role for m in rest] == ["user", "system", "user"]


class TestDisallowedBuiltIns:
    """The SDK falls back to its interactive built-in preset on no-tools
    turns; those built-ins (AskUserQuestion + the scheduling/planning/
    monitoring/worktree family) must be blocked so the brain can't emit
    them or model them as its own toolset (2026-05-26 WhatsApp incident).
    """

    def test_blocks_ask_user_question(self) -> None:
        assert "AskUserQuestion" in _DISALLOWED_BUILT_INS

    def test_blocks_leaked_builtin_categories(self) -> None:
        # The four categories the brain literally named when it confabulated
        # its toolset: scheduling / planning / monitoring / worktree.
        for name in (
            "EnterPlanMode", "ExitPlanMode",          # planning
            "Monitor",                                # monitoring
            "EnterWorktree", "ExitWorktree",          # worktree
            "CronCreate", "CronList", "CronDelete",   # scheduling
            "ScheduleWakeup",
        ):
            assert name in _DISALLOWED_BUILT_INS, f"{name} must be disallowed"

    def test_still_blocks_original_builtins(self) -> None:
        # Regression guard: the original blocked set must remain.
        for name in ("Bash", "Read", "Write", "Edit", "Task", "Skill"):
            assert name in _DISALLOWED_BUILT_INS


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
            options, _name_map = p._build_options(tools_spec=[], create_sdk_mcp_server=mock_server_factory)

        assert options.env["ANTHROPIC_API_KEY"] == ""
        assert options.env["ANTHROPIC_AUTH_TOKEN"] == ""
        assert options.env["ANTHROPIC_BASE_URL"] == ""

    def test_strict_mcp_config_enabled(self) -> None:
        """Without strict_mcp_config=True the SDK inherits the host
        user's global MCP config, leaking MCPs that lazyclaw's runtime
        doesn't know about."""
        p = ClaudeSDKProvider(model="sonnet")
        options, _name_map = p._build_options(
            tools_spec=[], create_sdk_mcp_server=MagicMock(return_value=MagicMock()),
        )
        assert options.strict_mcp_config is True

    def test_disallowed_built_ins_includes_toolsearch(self) -> None:
        """Newer Claude Code versions auto-fire ToolSearch before MCP
        tools — must be in the deny-list."""
        p = ClaudeSDKProvider(model="sonnet")
        options, _name_map = p._build_options(
            tools_spec=[], create_sdk_mcp_server=MagicMock(return_value=MagicMock()),
        )
        assert "ToolSearch" in options.disallowed_tools
        assert "Bash" in options.disallowed_tools
        assert "WebSearch" in options.disallowed_tools

    def test_permission_mode_bypass(self) -> None:
        """lazyclaw owns the permission system. SDK must not prompt."""
        p = ClaudeSDKProvider(model="sonnet")
        options, _name_map = p._build_options(
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


class TestReadOnlyListDedup:
    """Read-only listing tools collapse to FIRST occurrence regardless of
    args. Reproduces the 2026-05-16 incident: 7× upwork_get_messages with
    varied limit/filter args wasted ~20s of Cloudflare round-trips before
    the brain moved on."""

    def test_is_read_only_list_tool_recognizes_native_names(self) -> None:
        from lazyclaw.llm.providers.claude_sdk_provider import (
            _is_read_only_list_tool,
        )
        assert _is_read_only_list_tool("upwork_get_messages")
        assert _is_read_only_list_tool("list_jobs")
        assert _is_read_only_list_tool("list_tasks")
        assert _is_read_only_list_tool("vault_list")
        assert _is_read_only_list_tool("survival_status")

    def test_is_read_only_list_tool_recognizes_mcp_prefixed(self) -> None:
        from lazyclaw.llm.providers.claude_sdk_provider import (
            _is_read_only_list_tool,
        )
        # MCP-bridged form. The wrapper is ``mcp_<uuid>_<tool>``; in
        # production the bridge always uses uuid4, so the dedup helper
        # strips a strict UUID-shaped prefix and exact-matches the
        # remainder against ``_READ_ONLY_LIST_SUFFIXES``.
        assert _is_read_only_list_tool(
            "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_messages"
        )
        assert _is_read_only_list_tool(
            "mcp_11111111-2222-3333-4444-555555555555_upwork_get_contracts"
        )

    def test_is_read_only_list_tool_rejects_non_uuid_prefix(self) -> None:
        """Forward-fragility regression: a hypothetical future tool
        named ``acme_list_jobs`` or ``foo_upwork_get_messages`` must
        NOT be matched. The MCP bridge always uses uuid4, so a non-
        UUID prefix is either a hand-crafted lookalike or a brand-new
        tool — either way, suppressing its second call would lose
        legitimate brain output."""
        from lazyclaw.llm.providers.claude_sdk_provider import (
            _is_read_only_list_tool,
        )
        assert not _is_read_only_list_tool("mcp_xyz_upwork_get_contracts")
        assert not _is_read_only_list_tool("acme_list_jobs")
        assert not _is_read_only_list_tool("foo_upwork_get_messages")

    def test_is_read_only_list_tool_rejects_unrelated(self) -> None:
        from lazyclaw.llm.providers.claude_sdk_provider import (
            _is_read_only_list_tool,
        )
        # Tools that legitimately differ per call must NOT be collapsed
        assert not _is_read_only_list_tool("search_tools")
        assert not _is_read_only_list_tool("recall_memories")
        assert not _is_read_only_list_tool("web_search")
        assert not _is_read_only_list_tool("upwork_get_conversation")
        assert not _is_read_only_list_tool("upwork_send_message")
        assert not _is_read_only_list_tool("upwork_get_job_details")
        assert not _is_read_only_list_tool("save_note")
        assert not _is_read_only_list_tool("")

    def test_collapses_listing_calls_with_varied_args(self) -> None:
        """The 2026-05-16 pattern: same listing tool, different args."""
        tcs = [
            ToolCall(id="a", name="upwork_get_messages", arguments={"limit": 5}),
            ToolCall(id="b", name="upwork_get_messages", arguments={"limit": 10}),
            ToolCall(
                id="c", name="upwork_get_messages",
                arguments={"unread_only": True},
            ),
            ToolCall(
                id="d", name="upwork_get_messages",
                arguments={"limit": 20, "unread_only": False},
            ),
        ]
        out = _dedup_tool_calls(tcs)
        assert len(out) == 1
        assert out[0].id == "a"  # FIRST wins

    def test_collapses_mcp_prefixed_listing_calls(self) -> None:
        """Real-world: each call carries the mcp_<uuid>_ prefix."""
        n = "mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_get_messages"
        tcs = [
            ToolCall(id=f"t{i}", name=n, arguments={"limit": 5 + i})
            for i in range(7)  # exact 2026-05-16 count
        ]
        out = _dedup_tool_calls(tcs)
        assert len(out) == 1
        assert out[0].id == "t0"

    def test_listing_collapse_does_not_swallow_other_tools(self) -> None:
        """Mixed batch: 4× listing + 1× get_conversation must collapse to
        1× listing + 1× get_conversation (the conversation call is NOT
        in the read-only-list set)."""
        tcs = [
            ToolCall(id="a", name="upwork_get_messages", arguments={"limit": 5}),
            ToolCall(id="b", name="upwork_get_messages", arguments={"limit": 10}),
            ToolCall(id="c", name="upwork_get_messages", arguments={"limit": 15}),
            ToolCall(id="d", name="upwork_get_messages", arguments={"limit": 20}),
            ToolCall(
                id="e", name="upwork_get_conversation",
                arguments={"room_id": "room_xyz"},
            ),
        ]
        out = _dedup_tool_calls(tcs)
        assert len(out) == 2
        assert out[0].id == "a"
        assert out[1].id == "e"

    def test_listing_collapse_preserves_order_with_other_tools(self) -> None:
        """Order: tool_a, listing, tool_b, listing-dup, tool_c → tool_a,
        listing, tool_b, tool_c."""
        tcs = [
            ToolCall(id="1", name="search_tools", arguments={"q": "a"}),
            ToolCall(id="2", name="upwork_get_messages", arguments={"limit": 5}),
            ToolCall(id="3", name="search_tools", arguments={"q": "b"}),
            ToolCall(id="4", name="upwork_get_messages", arguments={"limit": 10}),
            ToolCall(id="5", name="recall_memories", arguments={"q": "c"}),
        ]
        out = _dedup_tool_calls(tcs)
        assert [tc.id for tc in out] == ["1", "2", "3", "5"]

    def test_different_listing_tools_both_survive(self) -> None:
        """One call to upwork_get_messages and one to upwork_get_contracts
        are different listings — both should survive."""
        tcs = [
            ToolCall(id="a", name="upwork_get_messages", arguments={}),
            ToolCall(id="b", name="upwork_get_contracts", arguments={}),
            ToolCall(id="c", name="list_jobs", arguments={}),
        ]
        out = _dedup_tool_calls(tcs)
        assert len(out) == 3

    def test_listing_dedup_logged_at_warning(self, caplog) -> None:
        """Dedup-drop count surfaces in WARNING logs for ops dashboards."""
        import logging
        tcs = [
            ToolCall(id=f"t{i}", name="upwork_get_messages",
                     arguments={"limit": 5 + i})
            for i in range(7)
        ]
        with caplog.at_level(logging.WARNING):
            _dedup_tool_calls(tcs)
        assert any("dropped" in rec.message for rec in caplog.records)


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
