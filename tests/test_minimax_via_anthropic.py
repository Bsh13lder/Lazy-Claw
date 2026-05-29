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


# ── MiniMax tool-discipline suffix ───────────────────────────────────────────


def test_is_minimax_brain_recognises_capitalisations() -> None:
    from lazyclaw.runtime.personality import is_minimax_brain
    assert is_minimax_brain("MiniMax-M2.7")
    assert is_minimax_brain("minimax-m2.5")
    assert is_minimax_brain("MiniMax-Text-01")
    assert is_minimax_brain("minimax-m2.7-highspeed")
    assert not is_minimax_brain("claude-sonnet-4-6")
    assert not is_minimax_brain("gemma4:e2b")
    assert not is_minimax_brain("")
    assert not is_minimax_brain(None)


def test_minimax_suffix_names_the_failure_modes() -> None:
    from lazyclaw.runtime.personality import minimax_tool_discipline_suffix
    suffix = minimax_tool_discipline_suffix()
    # Each phrase below corresponds to a failure mode the regex catches —
    # the suffix prevents the model from emitting them in the first place.
    for must_have in [
        "tool_use",
        "I'll notify",
        "Background task",
        "Dispatched",
        "Status: Running",
        "On it",
        "Stand by",
        "Sit tight",
    ]:
        assert must_have in suffix, f"missing {must_have!r}"


# ── tool_choice default ─────────────────────────────────────────────────────


def test_router_passes_default_tool_choice_auto_for_minimax() -> None:
    cfg = Config(
        minimax_api_key="mini-key",
        minimax_base_url="https://api.minimax.io/anthropic",
    )
    router = LLMRouter(cfg)
    provider = router._create_provider("minimax", "mini-key")
    assert isinstance(provider, AnthropicProvider)
    assert provider._default_tool_choice == {"type": "auto"}


def test_anthropic_provider_default_tool_choice_starts_none() -> None:
    p = AnthropicProvider(api_key="test-key")
    # Real Anthropic doesn't need a forced default; `auto` is already the
    # documented behaviour. Keep this distinction so plain Claude calls don't
    # send a redundant field.
    assert p._default_tool_choice is None


# ── tool count cap ──────────────────────────────────────────────────────────


def _make_tool(name: str, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_trim_tools_for_minimax_no_op_under_cap() -> None:
    from lazyclaw.runtime.agent import (
        _trim_tools_for_minimax, _BASE_TOOL_NAMES, _MINIMAX_MAX_TOOLS,
    )
    tools = [_make_tool(f"tool_{i}") for i in range(10)]
    result = _trim_tools_for_minimax(tools, "do something", set(_BASE_TOOL_NAMES))
    assert result == tools  # untouched


def test_trim_tools_for_minimax_keeps_base_and_picks_relevant() -> None:
    from lazyclaw.runtime.agent import (
        _trim_tools_for_minimax, _BASE_TOOL_NAMES, _MINIMAX_MAX_TOOLS,
    )
    base = [
        _make_tool("search_tools", "Discover tools"),
        _make_tool("delegate", "Dispatch to specialists"),
        _make_tool("recall_memories", ""),
        _make_tool("save_memory", ""),
        _make_tool("web_search", ""),
    ]
    irrelevant = [_make_tool(f"random_{i}", "junk noise filler") for i in range(20)]
    relevant = [
        _make_tool("send_gmail", "Send an email message via Gmail"),
        _make_tool("read_gmail", "Read inbox emails from Gmail"),
        _make_tool("append_sheet_rows", "Append rows to a Google Sheet"),
    ]
    tools = base + irrelevant + relevant
    assert len(tools) > _MINIMAX_MAX_TOOLS

    result = _trim_tools_for_minimax(
        tools, "send email and append a sheet row", set(_BASE_TOOL_NAMES),
    )
    assert len(result) == _MINIMAX_MAX_TOOLS
    names = {t["function"]["name"] for t in result}
    # Base tools always survive
    for n in {"search_tools", "delegate", "recall_memories", "save_memory", "web_search"}:
        assert n in names
    # Relevant tools survive (token overlap with the message)
    assert "send_gmail" in names
    assert "append_sheet_rows" in names


def test_trim_tools_for_minimax_falls_back_when_no_overlap() -> None:
    from lazyclaw.runtime.agent import (
        _trim_tools_for_minimax, _BASE_TOOL_NAMES, _MINIMAX_MAX_TOOLS,
    )
    base = [_make_tool("search_tools")]
    candidates = [_make_tool(f"unrelated_{i}", "") for i in range(40)]
    tools = base + candidates

    # Empty message → deterministic head-of-list fallback.
    result = _trim_tools_for_minimax(tools, "", set(_BASE_TOOL_NAMES))
    assert len(result) == _MINIMAX_MAX_TOOLS
    assert result[0]["function"]["name"] == "search_tools"


# ── _ACTION_CLAIM_RE corpus ─────────────────────────────────────────────────


def test_action_claim_regex_corpus_positives() -> None:
    """Every observed + synthetic narration string MUST match."""
    from lazyclaw.runtime.agent import _ACTION_CLAIM_RE

    corpus = [
        # Observed in the failing test run referenced in the plan file.
        "🚀 Background task running!",
        "Dispatched 3 search agents",
        "Status: Finding emails for: @x, @y, @z",
        "I'll notify you",
        # Adjacent patterns that should also be caught.
        "Already on it!",
        "Sit tight",
        "I just dispatched the task",
        "Roger that, running now",
        "On it!",
        "Working in background",
        "I'm now searching for the answer",
        "Results will arrive on Telegram shortly",
        "Started: ~just now",
        "Status: Running",
        "Status: Finding emails for: @example",
        "⏳ Running",
        "✅ Started",
        "Spinning up a worker",
        "Firing off a search",
        "Queued up the job",
        "I will keep you posted",
        "Got it, running",
        "In the meantime, feel free",
        "Hold on, searching",
        "Be right back with results",
        "Looking up info for: target",
        "Finding contacts for: @brand",
        "Searching for emails of brands",
        "I have queued the task",
        "I've scheduled the search",
        "i am running the search now",
        "Once it's done, I'll ping you",
    ]
    misses = [s for s in corpus if not _ACTION_CLAIM_RE.search(s)]
    assert misses == [], f"unmatched: {misses!r}"


def test_action_claim_regex_corpus_negatives() -> None:
    """Innocent prose must NOT match (false positives blow up the retry budget)."""
    from lazyclaw.runtime.agent import _ACTION_CLAIM_RE

    benign = [
        "Here is your data: 42",
        "The temperature is 24 degrees.",
        "Sources are listed below.",
        "You can find the answer in section 3.",
        "I checked the file and it works.",
        "The build passed.",
        "No errors detected.",
        "Searching the web is faster than browsing manually.",
    ]
    hits = [s for s in benign if _ACTION_CLAIM_RE.search(s)]
    assert hits == [], f"unexpected matches: {hits!r}"


def test_action_claim_regex_channel_read_refusal_family() -> None:
    """2026-05-29 "Chek my whats up + chek mail" incident.

    The background worker shipped a future-tense promise with tool_calls=0:
    "two readers are scanning WhatsApp and Email in parallel. I'll have your
    summary in a few seconds." None of the pre-existing verb patterns matched
    it, so the force-dispatch failsafe never fired and the promise was stored
    as the task result. These phrasings (observed + the variants the log
    forensics enumerated as MISSES) MUST now match.
    """
    from lazyclaw.runtime.agent import _ACTION_CLAIM_RE

    corpus = [
        # The exact promise that shipped.
        "two readers are scanning WhatsApp and Email in parallel. "
        "I'll have your summary in a few seconds.",
        # Variants enumerated as MISSES by the investigation.
        "I'll have your summary in a few seconds.",
        "I'll have your WhatsApp and email summary in a few seconds.",
        "Let me check your WhatsApp and email for you.",
        "One moment while I check.",
        "I'll summarize them for you momentarily.",
        "Pulling your messages now.",
        "Let me read your WhatsApp and email.",
        "Give me a sec while I pull your inbox.",
        "I'll send you a summary shortly.",
        "Two workers are fetching your messages.",
    ]
    misses = [s for s in corpus if not _ACTION_CLAIM_RE.search(s)]
    assert misses == [], f"unmatched: {misses!r}"


def test_action_claim_regex_channel_read_refusal_negatives() -> None:
    """The channel-read additions must NOT swallow legitimate no-tool prose."""
    from lazyclaw.runtime.agent import _ACTION_CLAIM_RE

    benign = [
        "Let me know if you want more detail.",
        "Here's a summary of the three options below.",
        "One option is to enable caching.",
        "I'll explain the trade-offs.",
        "Your WhatsApp is connected and 3 chats are unread.",
        "I read the file and everything looks correct.",
        "Reading the docs is the fastest way to learn this.",
    ]
    hits = [s for s in benign if _ACTION_CLAIM_RE.search(s)]
    assert hits == [], f"unexpected matches: {hits!r}"


# ── text-only counter ───────────────────────────────────────────────────────


def test_text_only_stats_starts_at_zero() -> None:
    p = AnthropicProvider(
        api_key="test-key",
        base_url="https://api.minimax.io/anthropic",
        disable_prompt_cache=True,
        default_model="MiniMax-M2.7",
    )
    stats = p.text_only_stats()
    assert stats == {"minimax_total_turns": 0, "minimax_text_only_turns": 0}
