"""Claude Agent SDK as an LLM provider.

The "sdk" transport for MODE_CLAUDE. Routes LLM calls through Anthropic's
official `claude-agent-sdk` Python package, which shells out to the local
`claude` binary the same way our legacy `claude_cli_provider.py` does —
so the call is still covered by the Claude Code subscription ($0
incremental cost until the June 15 2026 budget split).

Advantages over the raw CLI transport:
  - Native `tool_use` protocol — tools register as in-process MCP via
    `@tool` + `create_sdk_mcp_server`. No `--json-schema` text injection,
    no 60k-char tool-list serialised into the prompt every turn, no
    legacy `[TOOL_CALL]` regex fallback.
  - Typed response messages (`AssistantMessage` / `ResultMessage` /
    `ToolUseBlock`) — no stdout-JSON parsing.
  - `ResultMessage.total_cost_usd` for accurate cost reporting.
  - Built-in support for external MCP servers via `mcp_servers={...}`.

We keep the `BaseLLMProvider` contract identical to the CLI provider so
the rest of the codebase (agent runtime, taor, dispatcher) doesn't care
which transport is active. The eco_router picks per user_id based on
`users.settings.eco.claude_transport`.

Module-level imports are kept light; the SDK itself is imported lazily
inside `chat()` so a missing/broken install raises `SDKUnavailable` once
instead of breaking the whole provider module at boot.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from lazyclaw.llm.providers.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    StreamChunk,
    ToolCall,
)
from lazyclaw.llm.providers._disallowed_builtins import DISALLOWED_BUILT_INS

logger = logging.getLogger(__name__)


# Built-in Claude Code tools and MCP defaults that must always be blocked.
# Single source of truth lives in `_disallowed_builtins.py` so the SDK and
# legacy CLI transports can never drift apart again. See that module for
# the full rationale + incident history. Local alias keeps existing
# references (`_DISALLOWED_BUILT_INS`) unchanged.
_DISALLOWED_BUILT_INS = DISALLOWED_BUILT_INS

# Session isolation: pin the spawned claude process to a lazyclaw-owned cwd
# so its transcripts don't co-mingle with the user's personal Claude Code
# sessions. See _claude_home for the full leak rationale.
from lazyclaw.llm.providers._claude_home import isolated_claude_cwd as _isolated_claude_cwd

# MCP tool name pattern from lazyclaw's registry: `mcp_<uuid>_<tool_name>`.
# We strip the UUID prefix when registering with the SDK (cleaner names +
# tighter prompts) and reverse-map when dispatching the tool call back to
# lazyclaw's runtime.
import re
_MCP_UUID_RE = re.compile(
    r"^mcp_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_",
)

# Hallucinated hybrid form Sonnet occasionally emits when it confuses the
# registry's single-underscore convention with the SDK's double-underscore
# wrapping: `mcp__<uuid>__<tool>`. Observed 2026-05-14 on upwork_search_jobs
# — the call got dropped as "hallucinated" and the brain fell through to a
# clarification question, breaking the Upwork flow. We normalize it here so
# the short name still resolves via name_map.
_MCP_UUID_HALLUCINATED_RE = re.compile(
    r"^mcp__[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}__",
)

# SDK auto-prefixes our in-process MCP tools with `mcp__<server_name>__`
# when surfacing them in ToolUseBlocks. We strip it here so callers see
# the same names lazyclaw's registry knows.
_SDK_MCP_PREFIX = "mcp__lazyclaw__"


class SDKUnavailable(RuntimeError):
    """Raised when the SDK can't be reached (binary missing, auth missing,
    package not importable). Eco-router catches this to fall back to the
    legacy CLI transport. Tool-call errors and rate-limit / timeout errors
    are NOT wrapped in this — those are real bugs to surface.
    """


def _shorten_tool_name(name: str) -> str:
    """Strip the MCP UUID prefix for cleaner allowed_tools entries."""
    return _MCP_UUID_RE.sub("", name)


def _rewrite_tool_name_for_sdk(name: str) -> str:
    """Map a lazyclaw-registry tool name to the form the SDK exposes to Claude.

    The SDK wraps every lazyclaw skill as an in-process MCP tool under
    `mcp__lazyclaw__<short>`, where `<short>` is the registry name with
    the `mcp_<uuid>_` prefix stripped. When we serialize conversation
    history for the next turn we MUST use that exposed form — otherwise
    Sonnet sees `mcp_<uuid>_upwork_search_jobs` in its own prior
    [Assistant] tool calls, copies it on the next turn, and the SDK
    rejects the name because it's not in `allowed_tools`. The runtime
    then logs it as a hallucinated call. Observed ~50× / day on
    2026-05-14 before this rewrite was in place.

    Rules:
      - `mcp_<uuid>_<tool>` → `mcp__lazyclaw__<tool>`
      - everything else (built-in lazyclaw skills like `browser`,
        `search_tools`, `delegate`) is passed through untouched —
        those names ARE the names the SDK exposes (no UUID prefix to
        strip, no MCP wrapping above lazyclaw's own in-process server).
    """
    if _MCP_UUID_RE.match(name):
        short = _MCP_UUID_RE.sub("", name)
        return f"{_SDK_MCP_PREFIX}{short}"
    return name


def _split_leading_system(
    messages: list[LLMMessage],
) -> tuple[str, list[LLMMessage]]:
    """Pull the leading run of ``role == "system"`` messages off the front.

    The agent builds ``[system_prompt(=full SOUL.md), channel_context?,
    routing_hint?] + history + [user]`` (agent.py:2904-2934). Flattening
    that SOUL.md into a ``[System Context]`` text block buried in the
    prompt string gives Opus weak attention on the dispatch CORE LAW + F1
    grounding rules — a prime driver of the SDK path's tool-call refusals
    and confabulation. Routing those leading system messages into the
    SDK's NATIVE ``system_prompt`` field instead pins them at top priority
    (and lets the SDK/CLI cache them). Any non-leading system messages
    (e.g. a mid-history compaction marker) stay in the serialized body.

    Returns ``(joined_system_text, remaining_messages)``.
    """
    idx = 0
    leading: list[str] = []
    for msg in messages:
        if msg.role == "system" and msg.content:
            leading.append(msg.content)
            idx += 1
        else:
            break
    return "\n\n".join(leading), messages[idx:]


def _serialize_messages(messages: list[LLMMessage]) -> str:
    """Render the conversation as a single prompt string for the SDK.

    The SDK's `query()` accepts either a plain string or a streaming
    AsyncIterable of structured message dicts. We use the string form
    because our caller already curates the LLMMessage[] window — there's
    no benefit to round-tripping through the streaming-input format.

    Mirrors the CLI provider's `_serialize_messages` so behavior matches
    when toggling transports mid-session.
    """
    parts: list[str] = []
    for msg in messages:
        if msg.role == "system":
            parts.append(f"[System Context]\n{msg.content}\n")
        elif msg.role == "user":
            parts.append(f"[User]\n{msg.content}\n")
        elif msg.role == "assistant":
            if msg.tool_calls:
                tc_lines = []
                for tc in msg.tool_calls:
                    # Rewrite MCP tool names to the SDK-exposed form so
                    # the brain copies a name the SDK actually accepts.
                    # See `_rewrite_tool_name_for_sdk` for the rationale.
                    sdk_name = _rewrite_tool_name_for_sdk(tc.name)
                    tc_lines.append(
                        f"  -> {sdk_name}({tc.arguments})"
                    )
                tc_text = "\n".join(tc_lines)
                if msg.content:
                    parts.append(
                        f"[Assistant]\n{msg.content}\n{tc_text}\n"
                    )
                else:
                    parts.append(f"[Assistant]\n{tc_text}\n")
            else:
                parts.append(f"[Assistant]\n{msg.content}\n")
        elif msg.role == "tool":
            tid = msg.tool_call_id or "unknown"
            parts.append(f"[Tool Result: {tid}]\n{msg.content}\n")
    return "\n".join(parts)


def _build_tool_wrappers(
    tools: list[dict],
    dispatch: "callable | None" = None,
) -> tuple[list[Any], dict[str, str]]:
    """Wrap each neutral OpenAI-format tool dict as an SDK `@tool`.

    Returns (sdk_tool_list, short_to_full_name_map).

    Each wrapper:
      - Uses the SHORT name (UUID prefix stripped) so allowed_tools and
        Claude's tool-use blocks stay readable.
      - Calls `dispatch(short_name, args)` at runtime if a dispatcher is
        provided; otherwise returns a sentinel result that signals the
        caller to handle execution itself.

    The dispatcher pattern matters because lazyclaw's tool execution
    happens inside the agent runtime (taor.py), not in the SDK provider.
    When the SDK calls our @tool wrapper, we just need to bridge the call
    back to the agent. v1 returns a sentinel so the agent loop owns the
    actual side effects; v2 (later) can wire in-process dispatch directly.
    """
    from claude_agent_sdk import tool as sdk_tool

    sdk_tools: list[Any] = []
    name_map: dict[str, str] = {}

    for spec in tools:
        func = spec.get("function") or {}
        full_name = func.get("name") or ""
        if not full_name:
            continue
        short_name = _shorten_tool_name(full_name)
        if short_name != full_name:
            name_map[short_name] = full_name

        description = func.get("description") or ""
        # SDK accepts dict-form JSON Schema for input. We pass the neutral
        # `parameters` object straight through — same shape both Anthropic
        # and OpenAI tool APIs accept.
        input_schema = func.get("parameters") or {"type": "object", "properties": {}}

        # Bind `short_name` into the closure explicitly so the loop
        # variable doesn't leak across wrappers.
        def _make_wrapper(_short: str, _full: str):
            @sdk_tool(_short, description, input_schema)
            async def _wrapper(args: dict[str, Any]) -> dict[str, Any]:
                # v1: do NOT execute here. The SDK records the tool_use
                # block in the AssistantMessage stream; our provider's
                # `chat()` extracts it and returns it as `ToolCall` in
                # the LLMResponse. The agent runtime (taor.py) then
                # actually executes the tool via the skill registry, and
                # feeds the result back into the NEXT turn as an LLMMessage
                # with role="tool". That matches how the CLI provider
                # works today, so the agent loop is unchanged.
                #
                # We return a sentinel string here only as a safety net
                # in case the SDK ever invokes the wrapper directly
                # (e.g. inside a chained turn). The agent will refresh
                # this on the next iteration anyway.
                return {
                    "content": [{
                        "type": "text",
                        "text": "[lazyclaw runtime] tool call recorded — "
                                "agent will execute on next iteration",
                    }],
                }
            return _wrapper

        sdk_tools.append(_make_wrapper(short_name, full_name))

    return sdk_tools, name_map


def _result_error_text(
    *,
    is_error: bool,
    subtype: str,
    errors: "list | None",
    result: "str | None",
    has_output: bool,
) -> "str | None":
    """Map a ResultMessage error state to an error string, or None if benign.

    With ``max_turns=1`` (the one-turn-per-chat contract), a turn that ends
    because the model emitted tool calls reports ``subtype="error_max_turns"``
    — that is the EXPECTED outcome, not a failure: lazyclaw's runtime executes
    the harvested calls and continues its own loop. Only treat it as an error
    when the turn produced nothing usable at all.
    """
    if not is_error:
        return None
    if subtype == "error_max_turns" and has_output:
        return None
    errs = errors or [result or "unknown"]
    return "; ".join(str(e) for e in errs)


# Read-only listing tools that should NEVER be called more than once per
# assistant turn. Sonnet has a habit of "exploring" by re-issuing the same
# listing call with slightly-varied args (limit=5 vs limit=10 vs unread_only
# toggled) — the result set is functionally the same, but the (name, args)
# dedup misses them because the args differ. For these specific tools we
# collapse to the FIRST call regardless of args. Match is by tool name
# SUFFIX so the MCP-bridge prefix (mcp_<uuid>_<tool>) doesn't matter.
#
# DO NOT add tools that are legitimately called multiple times per turn
# with different args (search_tools, recall_memories, web_search, etc).
# Adding one here is a one-call-per-turn promise.
_READ_ONLY_LIST_SUFFIXES: frozenset[str] = frozenset({
    # Upwork MCP listings
    "upwork_get_messages",
    "upwork_get_contracts",
    "upwork_get_proposals",
    "upwork_get_unread_count",
    "upwork_get_my_profile",
    "upwork_get_profile_stats",
    "upwork_get_connects_balance",
    "upwork_check_session",
    "upwork_get_work_diary",
    # Lazyclaw native listings
    "list_jobs",
    "list_tasks",
    "list_contacts",
    "list_browser_templates",
    "list_browser_accounts",
    "list_live_browser_hosts",
    "list_watchers",
    "list_mcp_servers",
    "vault_list",
    "survival_status",
    "list_goals",
    "goal_status",
})


def _is_read_only_list_tool(name: str) -> bool:
    """True when ``name`` matches an entry in :data:`_READ_ONLY_LIST_SUFFIXES`.

    Two legal forms map to the dedup set:

      * The exact registry name (native skills like ``list_jobs``).
      * The MCP-bridged form ``mcp_<uuid>_<tool>`` — we strip the
        UUID-bearing prefix via :data:`_MCP_UUID_RE` and exact-match
        the remainder.

    Suffix-based ``endswith`` matching (the original 2026-05-14
    implementation) was forward-fragile: a hypothetical future tool
    named ``acme_list_jobs`` or ``foo_upwork_get_messages`` would have
    been silently deduped, dropping the brain's second call even
    though it's a genuinely different tool. Prefix-strip + exact match
    keeps the bridge support without that footgun.
    """
    name = (name or "").lower()
    if not name:
        return False
    if name in _READ_ONLY_LIST_SUFFIXES:
        return True
    # MCP-bridged tools are wrapped as ``mcp_<uuid>_<tool>`` in the
    # registry. Strip the wrapper and exact-match the remainder.
    if _MCP_UUID_RE.match(name):
        short = _MCP_UUID_RE.sub("", name)
        return short in _READ_ONLY_LIST_SUFFIXES
    return False


# Tool-DISCOVERY calls (finding tools by keyword) add nothing after the first
# few in a single turn — distinct-query spam is a panic signal, not real work.
# Capped per assistant turn inside _dedup_tool_calls (both brain + worker go
# through this provider). 2026-06-08: documents_specialist emitted ~19
# `search_tools` in one turn while thrashing a Google-Sheet task.
_DISCOVERY_TOOL_NAMES: frozenset[str] = frozenset({"search_tools"})
_DISCOVERY_TOOLS_CAP = 3


def _dedup_tool_calls(tool_calls: list[ToolCall]) -> list[ToolCall]:
    """Remove duplicate tool calls within one assistant turn.

    Two-pass dedup:

    1. Read-only listing tools (``_READ_ONLY_LIST_SUFFIXES``) are
       collapsed to their FIRST occurrence regardless of args. This
       catches the Sonnet "exploration loop" pattern where the brain
       re-issues ``upwork_get_messages`` with varied limit/filter args
       expecting a different answer (it always gets the same one, just
       slower). Saw this on 2026-05-16: 7× ``upwork_get_messages`` in
       one turn, only 2 caught by the (name,args) dedup, 4 wasted
       Cloudflare round-trips before the brain finally moved on.

    2. Everything else is keyed on (name, JSON-serialised args) —
       exact-match only. Legitimate parallel calls like
       search(q="python") + search(q="rust") survive.

    Sonnet occasionally emits the same tool with identical args 13×
    in a single response — most extreme observed 2026-05-14 after
    lazyclaw's dispatch primitives started returning errors and the
    brain panicked into carpet-bomb mode.

    The CLI provider's --json-schema constraint suppressed this
    implicitly via schema validation; the SDK path passes the raw
    tool_use blocks through untouched, so we filter here.

    Order is preserved so the runtime executes tools in the brain's
    intended sequence. Dropped tool_use_ids won't be referenced by any
    follow-up turn because the brain only sees one tool_result back
    per unique call.
    """
    if len(tool_calls) <= 1:
        return tool_calls

    seen_args: set[tuple[str, str]] = set()
    seen_listing_names: set[str] = set()
    discovery_count = 0
    deduped: list[ToolCall] = []
    for tc in tool_calls:
        # Pass 1: read-only listing — collapse by name regardless of args
        if _is_read_only_list_tool(tc.name):
            if tc.name in seen_listing_names:
                continue
            seen_listing_names.add(tc.name)
            deduped.append(tc)
            continue

        # Pass 1.5: cap tool-DISCOVERY spam per turn. Distinct queries survive
        # the exact-args dedup below, so a panicking model can emit ~19
        # `search_tools` in ONE turn. Keep the first _DISCOVERY_TOOLS_CAP; drop
        # the rest — a few searches are plenty to act on, and more only burn
        # tokens + trip the stuck detector. Brain AND worker hit this path.
        if tc.name in _DISCOVERY_TOOL_NAMES:
            if discovery_count >= _DISCOVERY_TOOLS_CAP:
                continue
            discovery_count += 1
            deduped.append(tc)
            continue

        # Pass 2: exact (name, args) dedup
        try:
            args_key = json.dumps(tc.arguments, sort_keys=True, default=str)
        except (TypeError, ValueError):
            # Args contain something non-JSON-serialisable; treat as
            # unique to avoid false-positive dedup of an exotic call.
            args_key = repr(tc.arguments)
        key = (tc.name, args_key)
        if key in seen_args:
            continue
        seen_args.add(key)
        deduped.append(tc)

    dropped = len(tool_calls) - len(deduped)
    if dropped:
        # Sonnet duplicating itself is a signal that something upstream
        # is degrading (broken dispatch primitive, oversized toolset).
        # Logging at WARNING surfaces it in the dashboard without
        # changing the agent's behaviour.
        logger.warning(
            "Claude SDK: dropped %d duplicate tool call(s) from one assistant "
            "turn (Sonnet emitted %d, deduped to %d). Most-duplicated tool: %s",
            dropped, len(tool_calls), len(deduped),
            _most_duplicated_name(tool_calls),
        )
    return deduped


def _most_duplicated_name(tool_calls: list[ToolCall]) -> str:
    """For logging — return the name that appeared most often in a batch."""
    from collections import Counter
    counter = Counter(tc.name for tc in tool_calls)
    name, count = counter.most_common(1)[0]
    return f"{name} (×{count})"


def _success_tail_action(
    err_str: str,
    have_usable_response: bool,
    already_retried: bool,
    upstream_error: str | None = None,
) -> str:
    """Classify an SDK iterator exception → "swallow" | "retry" | "raise".

    Targets the upstream quirk where the CLI emits a ResultMessage with
    is_error=True, errors=[], subtype="success" and the SDK surfaces it
    as a bare Exception "Claude Code returned an error result: success".
    With output already collected the turn actually succeeded → swallow.
    On an EMPTY turn the quirk wasted the whole call → retry ONCE
    (2026-06-09 09:33: a user-visible "Chat failed" on this exact shape).
    Anything else is a genuine iterator error → raise.

    `upstream_error` is the reason the CLI itself reported on the
    ResultMessage (``errors`` or ``result``). When it is set this is NOT
    the quirk — it is a real upstream failure wearing the quirk's text,
    because the CLI collapses ANY is_error result whose ``errors`` list
    is empty down to its bare subtype ("success"). Retrying cannot help
    and swallowing would hide it, so it always raises. 2026-07-25: an
    expired OAuth token made every call in the process take this path;
    each one burned a full pointless retry and then failed with a
    message that named no cause ("...error result: success"), while the
    actionable reason the CLI *did* report — "401 OAuth access token has
    expired. Re-authenticate to continue." — was discarded.
    """
    if upstream_error:
        return "raise"
    if "returned an error result: success" not in err_str:
        return "raise"
    if have_usable_response:
        return "swallow"
    if not already_retried:
        return "retry"
    return "raise"


def _max_turns_tail_action(
    err_str: str,
    have_usable_response: bool,
    already_retried: bool = False,
) -> "str | None":
    """Classify the max_turns=1 stop → "swallow" | "retry" | "raise" | None.

    The one-turn-per-chat contract (``max_turns=1`` in _build_options) is
    NOT surfaced by SDK 0.1.81 as a parseable ResultMessage: the internal
    reader raises a bare Exception on the message channel — "Claude Code
    returned an error result: Reached maximum number of turns (1)"
    (query.py receive_messages). The AssistantMessage has already streamed
    by then, so with text/tool calls harvested this is the EXPECTED end of
    a successful turn → "swallow".

    An EMPTY turn that hit the cap is the transient built-in-tool leak:
    the SDK-side model burns its single turn on one of the CLI's own
    tools and nothing reaches the harvester. 2026-08-20 19:23: this
    killed a specialist after 3 min of good work while the very next
    identical call succeeded — so retry ONCE (mirrors
    _success_tail_action's empty-turn retry), then "raise". Returns None
    when the exception is not this quirk (caller falls through to
    _success_tail_action).
    """
    if "Reached maximum number of turns" not in err_str:
        return None
    if have_usable_response:
        return "swallow"
    return "raise" if already_retried else "retry"


def _classify_oauth_credentials(
    cred: Any,
    now_ms: int,
) -> tuple[bool, str]:
    """Validate parsed `~/.claude/.credentials.json` → (ok, message).

    Existence of the file says nothing about whether the token still
    works. On 2026-07-25 the gateway booted "ready" with a token that
    had expired 37 days earlier AND carried an empty `refreshToken`, so
    the CLI could not renew it silently — every LLM call 401'd with no
    startup warning anywhere.

    Only that unrecoverable shape is reported. An expired token WITH a
    refresh token is fine (the CLI renews on next use), and an
    unrecognised schema is trusted rather than false-alarmed — this
    check must never block a boot it doesn't understand.
    """
    if not isinstance(cred, dict):
        return True, ""

    oauth = cred.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        oauth = cred

    expires_at = oauth.get("expiresAt")
    if not isinstance(expires_at, (int, float, str)):
        return True, ""  # Absent expiry — not ours to judge.
    try:
        expires_ms = int(expires_at)
    except ValueError:
        return True, ""  # Unparseable expiry — trust rather than block.

    if expires_ms > now_ms:
        return True, ""

    if str(oauth.get("refreshToken") or "").strip():
        return True, ""  # Expired but renewable — the CLI handles it.

    return False, (
        "Claude OAuth token EXPIRED and has no refresh token — every "
        "LLM call will fail with HTTP 401. Note `claude auth status` "
        "still reports loggedIn:true here, so it cannot be used to "
        "confirm. Re-authenticate: "
        "`docker exec -it lazyclaw claude auth login`."
    )


def _find_claude_binary() -> str | None:
    """Search well-known paths for the `claude` binary.

    The SDK can auto-locate it on most systems, but if our server runs
    with a stripped PATH (Docker, launchd) it may not. Returning a path
    here lets us pass `cli_path=` to ClaudeAgentOptions.
    """
    import shutil
    found = shutil.which("claude")
    if found:
        return found
    home = os.path.expanduser("~")
    for cand in (
        os.path.join(home, ".local", "bin", "claude"),
        os.path.join(home, "bin", "claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


class ClaudeSDKProvider(BaseLLMProvider):
    """LLM provider that talks to `claude` via the official Agent SDK.

    One provider instance per user_id (matches the legacy CLI provider's
    lifecycle in eco_router). State held here:
      - `_model` — current `--model` choice (sonnet/opus/haiku)
      - `_claude_bin` — resolved binary path
      - `_tool_name_map` — kept for back-compat / introspection only.
        Reflects the LAST completed chat() call. Concurrent chat()
        callers MUST NOT rely on it — each call now binds its own local
        name_map via _build_options return value and threads it through
        _unmap_tool_name. The provider is shared per-process (one cached
        on EcoRouter), so a serializing lock would re-create the
        foreground-blocking bug background sub-agents already fight.
    """

    PROVIDER_NAME = "claude_sdk"

    def __init__(
        self,
        model: str = "sonnet",
        claude_bin: str | None = None,
    ) -> None:
        self._model = model
        self._claude_bin = claude_bin or _find_claude_binary()
        self._tool_name_map: dict[str, str] = {}

    async def verify_key(self) -> bool:
        """Health check — confirm SDK importable and binary findable.

        We don't actually spin a query here (would consume subscription
        for a trivial probe); just verify the prerequisites.
        """
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            logger.debug(
                "[provider:claude_sdk] verify_key: claude-agent-sdk not importable",
            )
            return False
        if self._claude_bin is None:
            logger.debug(
                "[provider:claude_sdk] verify_key: `claude` binary not found",
            )
            return False
        return True

    async def health_check(self) -> bool:
        return await self.verify_key()

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str = "",
        **kwargs: Any,
    ) -> LLMResponse:
        """Send the conversation to Claude via the Agent SDK.

        Args:
            messages: LLMMessage[] — full conversation window (caller
                already curated this, we don't trim).
            model: Ignored. The provider uses `self._model` to honor the
                role-resolved model from eco_router (`_resolve_claude_cli_model`).
            **kwargs:
                tools: list[dict] — neutral OpenAI-format tool specs from
                    the skill registry. We wrap each as an SDK @tool.
                session_id: str (optional) — derived deterministically by
                    the caller from user_id + context_id. Currently unused
                    in v1 — see _build_options for details.

        Returns:
            LLMResponse with `content`, `tool_calls`, and `usage` populated.

        Raises:
            SDKUnavailable: SDK package missing or `claude` binary missing.
                Caller (eco_router) catches this and falls back to the
                legacy CLI transport.
            RuntimeError: API errors, billing errors, malformed responses.
                NOT caught by the fallback — these are real bugs.
        """
        try:
            from claude_agent_sdk import (
                query as sdk_query,
                ClaudeAgentOptions,
                AssistantMessage,
                ResultMessage,
                TextBlock,
                ToolUseBlock,
                ThinkingBlock,
                CLINotFoundError,
                CLIConnectionError,
                ProcessError,
                ClaudeSDKError,
            )
            from claude_agent_sdk import create_sdk_mcp_server
        except ImportError as exc:
            raise SDKUnavailable(f"claude-agent-sdk not installed: {exc}") from exc

        if self._claude_bin is None:
            # Last-chance lookup — PATH may have changed since __init__.
            self._claude_bin = _find_claude_binary()
            if self._claude_bin is None:
                raise SDKUnavailable(
                    "`claude` CLI binary not found in PATH or well-known "
                    "locations. Install Claude Code: "
                    "https://docs.anthropic.com/en/docs/claude-code"
                )

        tools_spec: list[dict] = kwargs.get("tools") or []

        # Build prompt + options. ``name_map`` is bound LOCALLY here so
        # concurrent chat() callers on the same provider instance don't
        # clobber each other's short→full tool-name resolution. See the
        # class docstring for the race window history.
        _system_text, _body_messages = _split_leading_system(messages)
        prompt_text = _serialize_messages(_body_messages)
        options, name_map = self._build_options(
            tools_spec, create_sdk_mcp_server, system_prompt=_system_text or None,
            user_id=kwargs.get("user_id"),
        )

        logger.info(
            "Claude SDK call: tools=%d, model=%s, prompt_len=%d chars",
            len(tools_spec), self._model, len(prompt_text),
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage_raw: dict[str, Any] | None = None
        api_equiv_cost: float = 0.0
        stop_reason: str | None = None
        session_id: str | None = None
        api_error: str | None = None
        _t0 = time.monotonic()

        try:
            async for msg in sdk_query(prompt=prompt_text, options=options):
                cls = type(msg).__name__

                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            # Strip SDK MCP prefix, then reverse-map
                            # short→full so the registry finds the skill.
                            registry_name = self._unmap_tool_name(block.name, name_map)
                            # Drop Claude Code built-ins (Skill,
                            # run_command, SlashCommand, …) that leak
                            # past `disallowed_tools`. The SDK's
                            # disallowed_tools blocks execution but
                            # NOT emission — Sonnet still produces
                            # tool_use blocks for them from training
                            # recall, polluting our history rendering
                            # and wasting tool slots in the batch.
                            # See _DISALLOWED_BUILT_INS for the full
                            # list and commit 96c1181 for the original
                            # block attempt (which only fixed exec).
                            if (
                                block.name in _DISALLOWED_BUILT_INS
                                or registry_name in _DISALLOWED_BUILT_INS
                            ):
                                logger.warning(
                                    "Claude SDK: dropped built-in tool_use block %r "
                                    "(Claude Code surface — not a lazyclaw skill)",
                                    block.name,
                                )
                                continue
                            tool_calls.append(ToolCall(
                                id=block.id or f"sdk_{uuid.uuid4().hex[:8]}",
                                name=registry_name,
                                arguments=block.input or {},
                            ))
                        # ThinkingBlock / ToolResultBlock / Server* blocks
                        # are NOT promoted into our LLMResponse: they're
                        # SDK-side bookkeeping that the agent loop doesn't
                        # need to see again.

                elif isinstance(msg, ResultMessage):
                    usage_raw = msg.usage
                    api_equiv_cost = float(msg.total_cost_usd or 0.0)
                    stop_reason = msg.stop_reason
                    session_id = msg.session_id
                    # Upstream errors surface as a clear runtime failure
                    # (NOT SDKUnavailable, so eco_router doesn't silently
                    # fall back over a legitimate API error). A max_turns=1
                    # stop with harvested output is the expected success
                    # path, not an error — see _result_error_text.
                    api_error = _result_error_text(
                        is_error=msg.is_error,
                        subtype=getattr(msg, "subtype", "") or "",
                        errors=msg.errors,
                        result=msg.result,
                        has_output=bool(tool_calls or text_parts),
                    )

                # SystemMessage / RateLimitEvent / HookEventMessage /
                # StreamEvent — observability only, nothing to extract.

        except CLINotFoundError as exc:
            raise SDKUnavailable(f"`claude` binary missing: {exc}") from exc
        except CLIConnectionError as exc:
            raise SDKUnavailable(f"Claude CLI connection failed: {exc}") from exc
        except ProcessError as exc:
            # Subprocess exited non-zero. Most common cause: not logged
            # in. Surface a clear error so the caller can prompt for
            # `claude /login`.
            msg_lower = str(exc).lower()
            if "not logged in" in msg_lower or "/login" in msg_lower:
                raise SDKUnavailable(
                    "Claude CLI is not logged in. Run `claude /login` "
                    "on the host (or `docker exec -it lazyclaw claude /login` "
                    "in the container) and retry."
                ) from exc
            raise RuntimeError(f"Claude SDK process error: {exc}") from exc
        except ClaudeSDKError as exc:
            raise RuntimeError(f"Claude SDK error: {exc}") from exc
        except Exception as exc:
            # SDK upstream quirk: when the CLI emits a ResultMessage with
            # is_error=True AND errors=[] AND subtype="success", the SDK's
            # internal reader (query.py:306-308) falls back to using the
            # subtype string as the error text, then catches the trailing
            # ProcessError and re-wraps it as the literal message
            # "Claude Code returned an error result: success". The SDK
            # surfaces that as a bare `Exception` via its message channel
            # (query.py:851-852), which doesn't match any of the typed
            # except clauses above. Result: a turn that actually
            # SUCCEEDED — assistant text + tool calls already collected —
            # gets reported as a chat failure to the agent runtime.
            # Observed twice on 2026-05-14.
            #
            # If we have a usable response in hand (content or tool_calls,
            # no upstream api_error), absorb the trailing exception and
            # return normally. Otherwise re-wrap and re-raise so genuine
            # iterator errors still surface.
            _usable = bool((text_parts or tool_calls) and not api_error)
            _mt_action = _max_turns_tail_action(
                str(exc), _usable,
                already_retried=bool(kwargs.get("_max_turns_retried")),
            )
            if _mt_action == "swallow":
                # The by-design max_turns=1 stop — the turn succeeded and
                # lazyclaw's runtime executes the harvested calls next.
                logger.debug(
                    "Claude SDK: absorbed max_turns=1 stop (%d text chars "
                    "+ %d tool call(s))",
                    sum(len(t) for t in text_parts), len(tool_calls),
                )
                action = "swallow"
            elif _mt_action == "retry":
                # Zero-output cap hit: the SDK-side model burned its one
                # turn on a built-in tool — transient, nothing ran on our
                # side, so ONE full retry is safe (2026-08-20 19:23).
                logger.warning(
                    "Claude SDK: turn hit max_turns=1 with NOTHING "
                    "harvested (built-in tool burned the turn) — "
                    "retrying once"
                )
                return await self.chat(
                    messages, model,
                    **{**kwargs, "_max_turns_retried": True},
                )
            elif _mt_action == "raise":
                raise RuntimeError(
                    "Claude SDK: turn hit max_turns=1 with no output — "
                    f"nothing harvested before: {exc}"
                ) from exc
            else:
                action = _success_tail_action(
                    str(exc),
                    have_usable_response=_usable,
                    already_retried=bool(kwargs.get("_success_tail_retried")),
                    upstream_error=api_error,
                )
            if action == "swallow":
                if _mt_action is None:
                    logger.warning(
                        "Claude SDK: swallowed trailing 'error result: "
                        "success' (turn succeeded — %d text chars + %d tool "
                        "call(s))",
                        sum(len(t) for t in text_parts), len(tool_calls),
                    )
            elif action == "retry":
                # Empty turn killed by the quirk — nothing was produced
                # and no lazyclaw tool ran, so ONE full retry is safe.
                logger.warning(
                    "Claude SDK: empty turn ended with 'error result: "
                    "success' (SDK quirk) — retrying once"
                )
                return await self.chat(
                    messages, model,
                    **{**kwargs, "_success_tail_retried": True},
                )
            elif api_error:
                # The CLI told us WHY it failed; the trailing
                # ProcessError text ("...error result: success") did
                # not. Surface the reason, not the placeholder.
                raise RuntimeError(
                    f"Claude SDK upstream error: {api_error}"
                ) from exc
            else:
                raise RuntimeError(
                    f"Claude SDK iterator error: {exc}"
                ) from exc

        if api_error:
            raise RuntimeError(f"Claude SDK upstream error: {api_error}")

        content = "".join(text_parts).strip()
        usage = self._normalize_usage(usage_raw, api_equiv_cost)
        if stop_reason:
            usage["stop_reason"] = stop_reason
        if session_id:
            usage["session_id"] = session_id

        # Dedup pass — Sonnet under load (high-tool-count batches +
        # broken dispatch paths) occasionally emits the same (name, args)
        # tool call multiple times in one assistant turn. The CLI
        # provider's --json-schema validation suppressed this implicitly;
        # the SDK passes the raw tool_use blocks through, so we filter
        # here. Observed 2026-05-14: 13× google_run_task with identical
        # args in one batch.
        #
        # Dedup key = (name, JSON-serialised args). Order-preserving:
        # keeps the FIRST instance so the tool_use_id of duplicates is
        # dropped, which is correct because the runtime only needs to
        # execute the call once anyway.
        if tool_calls:
            tool_calls = _dedup_tool_calls(tool_calls)
            logger.info(
                "Claude SDK returned %d tool calls: %s",
                len(tool_calls), [tc.name for tc in tool_calls],
            )

        # Provider call boundary summary — latency + finish + token/cost
        # telemetry (never prompt/response text).
        logger.debug(
            "[provider:claude_sdk] chat done model=%s latency=%dms stop=%s "
            "tool_calls=%d tokens_in=%s tokens_out=%s api_equiv_cost=$%.4f",
            self._model, int((time.monotonic() - _t0) * 1000), stop_reason,
            len(tool_calls or []),
            usage.get("prompt_tokens"), usage.get("completion_tokens"),
            api_equiv_cost,
        )

        return LLMResponse(
            content=content,
            model=f"claude-sdk ({self._model})",
            usage=usage,
            tool_calls=tool_calls or None,
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        model: str = "",
        **kwargs: Any,
    ):
        """Real streaming — yields one ``StreamChunk`` per ``TextBlock``
        as the SDK emits them, plus a terminal ``done=True`` chunk
        carrying the accumulated tool calls + usage.

        Mirrors ``chat()``'s SDK-iteration logic but emits incrementally
        so the Web UI sees text tokens land in real time instead of
        waiting for the whole turn to complete (legacy v1 collapsed to
        ONE final chunk — Web UI looked frozen for 60–120s under
        MODE_CLAUDE).

        Tool calls are NOT streamed mid-turn: they're accumulated and
        attached to the terminal chunk so taor.py dispatches them in
        one batch on the next turn (same contract as ``chat()``).

        Raises ``SDKUnavailable`` / ``RuntimeError`` identically to
        ``chat()`` so the eco_router fallback semantics stay the same.
        """
        try:
            from claude_agent_sdk import (
                query as sdk_query,
                ClaudeAgentOptions,
                AssistantMessage,
                ResultMessage,
                TextBlock,
                ToolUseBlock,
                CLINotFoundError,
                CLIConnectionError,
                ProcessError,
                ClaudeSDKError,
            )
            from claude_agent_sdk import create_sdk_mcp_server
        except ImportError as exc:
            raise SDKUnavailable(f"claude-agent-sdk not installed: {exc}") from exc

        if self._claude_bin is None:
            self._claude_bin = _find_claude_binary()
            if self._claude_bin is None:
                raise SDKUnavailable(
                    "`claude` CLI binary not found in PATH or well-known "
                    "locations."
                )

        tools_spec: list[dict] = kwargs.get("tools") or []
        _system_text, _body_messages = _split_leading_system(messages)
        prompt_text = _serialize_messages(_body_messages)
        options, name_map = self._build_options(
            tools_spec, create_sdk_mcp_server, system_prompt=_system_text or None,
            user_id=kwargs.get("user_id"),
        )

        logger.info(
            "Claude SDK stream: tools=%d, model=%s, prompt_len=%d chars",
            len(tools_spec), self._model, len(prompt_text),
        )

        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []  # for the success-tail absorption guard
        usage_raw: dict[str, Any] | None = None
        api_equiv_cost: float = 0.0
        stop_reason: str | None = None
        session_id: str | None = None
        api_error: str | None = None
        model_label = f"claude-sdk ({self._model})"
        _t0 = time.monotonic()

        try:
            async for msg in sdk_query(prompt=prompt_text, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                            yield StreamChunk(
                                delta=block.text,
                                model=model_label,
                                done=False,
                            )
                        elif isinstance(block, ToolUseBlock):
                            registry_name = self._unmap_tool_name(
                                block.name, name_map,
                            )
                            if (
                                block.name in _DISALLOWED_BUILT_INS
                                or registry_name in _DISALLOWED_BUILT_INS
                            ):
                                logger.warning(
                                    "Claude SDK stream: dropped built-in tool "
                                    "%r (not a lazyclaw skill)",
                                    block.name,
                                )
                                continue
                            tool_calls.append(ToolCall(
                                id=block.id or f"sdk_{uuid.uuid4().hex[:8]}",
                                name=registry_name,
                                arguments=block.input or {},
                            ))
                elif isinstance(msg, ResultMessage):
                    usage_raw = msg.usage
                    api_equiv_cost = float(msg.total_cost_usd or 0.0)
                    stop_reason = msg.stop_reason
                    session_id = msg.session_id
                    # Same contract as chat(): a max_turns=1 stop with
                    # harvested output is success, not an error.
                    api_error = _result_error_text(
                        is_error=msg.is_error,
                        subtype=getattr(msg, "subtype", "") or "",
                        errors=msg.errors,
                        result=msg.result,
                        has_output=bool(tool_calls or text_parts),
                    )
        except CLINotFoundError as exc:
            raise SDKUnavailable(f"`claude` binary missing: {exc}") from exc
        except CLIConnectionError as exc:
            raise SDKUnavailable(f"Claude CLI connection failed: {exc}") from exc
        except ProcessError as exc:
            msg_lower = str(exc).lower()
            if "not logged in" in msg_lower or "/login" in msg_lower:
                raise SDKUnavailable(
                    "Claude CLI is not logged in. Run `claude /login` "
                    "and retry."
                ) from exc
            raise RuntimeError(f"Claude SDK stream process error: {exc}") from exc
        except ClaudeSDKError as exc:
            raise RuntimeError(f"Claude SDK stream error: {exc}") from exc
        except Exception as exc:
            # Same absorption guards as chat(): the by-design max_turns=1
            # stop first, then the "error result: success" quirk.
            err_str = str(exc)
            have_usable_response = bool(
                (text_parts or tool_calls) and not api_error
            )
            _mt_action = _max_turns_tail_action(err_str, have_usable_response)
            looks_like_success_tail = (
                "returned an error result: success" in err_str
            )
            if _mt_action == "swallow":
                logger.debug(
                    "Claude SDK stream: absorbed max_turns=1 stop "
                    "(%d tool call(s))", len(tool_calls),
                )
            elif looks_like_success_tail and have_usable_response:
                logger.warning(
                    "Claude SDK stream: swallowed trailing 'error result: "
                    "success' (turn succeeded)",
                )
            else:
                raise RuntimeError(
                    f"Claude SDK stream iterator error: {exc}"
                ) from exc

        if api_error:
            raise RuntimeError(f"Claude SDK upstream error: {api_error}")

        # Dedup tool calls (same logic as chat()).
        if tool_calls:
            tool_calls = _dedup_tool_calls(tool_calls)

        usage = self._normalize_usage(usage_raw, api_equiv_cost)
        if stop_reason:
            usage["stop_reason"] = stop_reason
        if session_id:
            usage["session_id"] = session_id

        logger.debug(
            "[provider:claude_sdk] stream done model=%s latency=%dms stop=%s "
            "tool_calls=%d tokens_in=%s tokens_out=%s",
            self._model, int((time.monotonic() - _t0) * 1000), stop_reason,
            len(tool_calls or []),
            usage.get("prompt_tokens"), usage.get("completion_tokens"),
        )

        # Terminal chunk: empty delta, tool calls + usage attached.
        yield StreamChunk(
            delta="",
            tool_calls=tool_calls or None,
            usage=usage,
            model=model_label,
            done=True,
        )

    # ── internals ──────────────────────────────────────────────────────

    def _unmap_tool_name(
        self,
        sdk_name: str,
        name_map: dict[str, str] | None = None,
    ) -> str:
        """Reverse the two transformations done in `_build_tool_wrappers`:
        1. SDK prefixes in-process MCP tool names with `mcp__lazyclaw__`.
        2. We stripped the registry's `mcp_<uuid>_` prefix when registering.

        Given `mcp__lazyclaw__instagram_read_profile` we want the original
        `mcp_<uuid>_instagram_read_profile` back so the skill registry can
        look up the right MCP tool.

        Also normalizes two hallucinated forms Sonnet emits when it crosses
        the wires between the registry's single-underscore convention and
        the SDK's double-underscore wrapping:
          - `mcp__<uuid>__<tool>` (hybrid; observed on Upwork flow 2026-05-14)
          - `mcp_<uuid>_<tool>`   (raw registry form copied from prior turn)
        Both map to `<tool>` and resolve via name_map exactly like the
        canonical SDK form.
        """
        short = sdk_name
        if short.startswith(_SDK_MCP_PREFIX):
            short = short[len(_SDK_MCP_PREFIX):]
        elif _MCP_UUID_HALLUCINATED_RE.match(short):
            short = _MCP_UUID_HALLUCINATED_RE.sub("", short)
            logger.warning(
                "Claude SDK: rescued hallucinated MCP tool name %r "
                "(hybrid uuid/sdk form) → %r",
                sdk_name, short,
            )
        elif _MCP_UUID_RE.match(short):
            short = _MCP_UUID_RE.sub("", short)
            logger.warning(
                "Claude SDK: rescued raw registry MCP tool name %r → %r",
                sdk_name, short,
            )
        # Prefer caller-supplied per-call map (concurrent-safe); fall
        # back to self for legacy single-call usage.
        map_to_use = name_map if name_map is not None else self._tool_name_map
        return map_to_use.get(short, short)

    def _build_options(
        self,
        tools_spec: list[dict],
        create_sdk_mcp_server,
        system_prompt: str | None = None,
        user_id: str | None = None,
    ) -> tuple[Any, dict[str, str]]:
        """Construct ClaudeAgentOptions for one chat() call.

        Builds:
          - In-process MCP server bundling all lazyclaw skills as @tool
          - allowed_tools list with the mcp__lazyclaw__<name> prefix
          - disallowed_tools list with Claude Code's built-ins (so they
            don't fire instead of our skills)
          - env dict with ANTHROPIC_API_KEY stripped (still load-bearing
            per upstream issue #12352 — env var preempts subscription auth)
          - permission_mode = bypassPermissions (lazyclaw owns the
            permission system via _DISALLOWED_BUILT_INS, _allow_*, /allow,
            and per-skill permissions)
        """
        from claude_agent_sdk import ClaudeAgentOptions

        sdk_tools, name_map = _build_tool_wrappers(tools_spec)
        # Mirror onto self for back-compat / introspection only. Concurrent
        # callers must use the returned ``name_map`` for correctness — see
        # class docstring.
        self._tool_name_map = name_map

        mcp_servers: dict[str, Any] = {}
        allowed_tools: list[str] = []

        if sdk_tools:
            server_config = create_sdk_mcp_server(
                name="lazyclaw",
                version="1.0.0",
                tools=sdk_tools,
            )
            mcp_servers["lazyclaw"] = server_config
            allowed_tools = [
                f"mcp__lazyclaw__{t.name}" for t in sdk_tools
            ]

        # Force OAuth subscription auth.
        # The SDK MERGES our env dict on top of `os.environ` rather than
        # replacing it (see claude_agent_sdk/_internal/transport/
        # subprocess_cli.py lines 430-436). So just omitting
        # ANTHROPIC_API_KEY from this dict is NOT enough — the value
        # would still be inherited from the parent process (loaded by
        # `load_dotenv()` in config.py for the Anthropic API path used
        # by HYBRID/FULL modes). We must explicitly set the keys to
        # empty strings so the merge overwrites the inherited values
        # with "unset". claude's auth precedence is API_KEY > AUTH_TOKEN
        # > OAuth subscription, so any non-empty value preempts the
        # subscription. An empty string disables that branch and falls
        # through to OAuth.
        #
        # Also wipes ANTHROPIC_AUTH_TOKEN and ANTHROPIC_BASE_URL because
        # both can hijack auth or point at a non-Anthropic endpoint.
        env = {
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_BASE_URL": "",
        }

        kw: dict[str, Any] = {
            "model": self._model,
            "permission_mode": "bypassPermissions",
            "disallowed_tools": _DISALLOWED_BUILT_INS,
            "env": env,
            # CRITICAL (one-turn-per-chat contract): our @tool wrappers do
            # NOT execute anything — they return a sentinel, and the agent
            # runtime executes the harvested calls itself. Without this cap
            # the SDK runs its OWN internal loop, feeding that sentinel back
            # to the model as the tool "result": the model retried browser
            # calls 5-9× per batch against placeholder results, then
            # escalated ("returned nothing but the placeholder string for 9
            # consecutive calls") — 2026-08-16/17 himap incidents. One turn
            # per chat() means the model never sees the sentinel; a turn
            # ending in tool calls reports subtype="error_max_turns", which
            # _result_error_text treats as the expected success path.
            "max_turns": 1,
            # CRITICAL: without this the SDK inherits the host user's
            # global Claude Code MCP config (~/.claude/mcp_servers.json)
            # — meaning Sonnet sees and uses globally-registered MCPs
            # (Canva, Gmail, host Upwork, etc.) that lazyclaw's runtime
            # has NO knowledge of. Tool dispatch then crashes because
            # the agent can't execute a tool that isn't in its registry.
            # strict_mcp_config=True locks the surface down to ONLY the
            # MCPs we explicitly pass in (the lazyclaw in-process server).
            "strict_mcp_config": True,
            # CRITICAL (session isolation — inbound leak fix): with no
            # setting_sources the SDK defaults to "all sources loaded"
            # (CLI default), pulling the HOST USER's personal Claude Code
            # state into lazyclaw's agent context — ~/.claude/settings.json,
            # ~/.claude/CLAUDE.md, ~/.claude/rules/*, AND any personal hooks
            # (SessionStart, etc.). [] = SDK isolation mode: the agent gets
            # ONLY what lazyclaw passes (SOUL.md via system_prompt + the
            # lazyclaw MCP tools). lazyclaw's own personality is SOUL.md,
            # never the repo/user CLAUDE.md.
            "setting_sources": [],
            # CRITICAL (session isolation — outbound leak fix): pin cwd to a
            # lazyclaw-owned dir so the SDK's auto-persisted .jsonl transcript
            # (full decrypted conversation) lands in its OWN project bucket
            # instead of co-mingling with the user's personal Claude Code
            # sessions in the inherited process cwd. Credentials stay at
            # ~/.claude/.credentials.json (CLAUDE_CONFIG_DIR untouched) so
            # OAuth subscription auth is unaffected. Scoped per user_id so
            # multi-tenant deployments don't co-mingle agent transcripts.
            "cwd": _isolated_claude_cwd(user_id),
        }
        if mcp_servers:
            kw["mcp_servers"] = mcp_servers
            kw["allowed_tools"] = allowed_tools
        if self._claude_bin:
            kw["cli_path"] = self._claude_bin

        # System prompt: prefer the caller's full system content (SOUL.md +
        # channel/routing context) routed into the SDK's NATIVE system field
        # so the dispatch CORE LAW + F1 grounding rules get top attention
        # priority (and SDK/CLI caching) instead of being buried in a
        # serialized [System Context] body block. Fall back to a brief
        # identity stub only when the caller passed no system content.
        kw["system_prompt"] = system_prompt or (
            "You are LazyClaw, an AI agent. Any [System Context] blocks "
            "in the conversation contain your project rules. Use tools "
            "from the MCP server when the user wants action; answer "
            "directly otherwise."
        )

        return ClaudeAgentOptions(**kw), name_map

    @staticmethod
    def _normalize_usage(
        usage_raw: dict | None,
        api_equiv_cost: float,
    ) -> dict:
        """Translate SDK usage dict → our usage dict.

        `cost_usd` reports $0 because the call is subscription-funded
        until June 15 2026. `cost_usd_subscription` retains the
        API-equivalent value for diagnostics ("how much would this have
        cost?"). Cache token fields pass through so the dashboard's
        prompt-caching panel keeps working.
        """
        u = usage_raw or {}
        return {
            "prompt_tokens": u.get("input_tokens", 0),
            "completion_tokens": u.get("output_tokens", 0),
            "total_tokens": (
                u.get("input_tokens", 0) + u.get("output_tokens", 0)
            ),
            "cache_read_tokens": u.get("cache_read_input_tokens", 0),
            "cache_creation_tokens": u.get("cache_creation_input_tokens", 0),
            "cost_usd": 0.0,
            "cost_usd_subscription": api_equiv_cost,
            "provider": ClaudeSDKProvider.PROVIDER_NAME,
        }


def check_claude_sdk_auth() -> tuple[bool, str]:
    """Boot-time readiness check — silent when OK, loud when broken.

    Mirrors `check_claude_cli_auth()` so the gateway startup banner can
    show whichever transport(s) are usable. We can't probe Keychain on
    macOS hosts (no Foundation bindings) so we trust it; on Linux/Docker
    we check `~/.claude/.credentials.json`.
    """
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        logger.warning(
            "[provider:claude_sdk] auth check: claude-agent-sdk not installed",
        )
        return False, (
            "claude-agent-sdk is not installed. Run "
            "`pip install claude-agent-sdk>=0.1.81` or rebuild Docker."
        )

    if _find_claude_binary() is None:
        logger.debug(
            "[provider:claude_sdk] auth check: no `claude` binary — SDK unused",
        )
        return True, ""  # SDK unused — silent.

    import sys
    if sys.platform == "darwin" and not os.path.exists("/.dockerenv"):
        logger.debug(
            "[provider:claude_sdk] auth check: macOS host — trusting Keychain cred",
        )
        return True, ""

    cred_path = os.path.expanduser("~/.claude/.credentials.json")
    if not os.path.isfile(cred_path) or os.path.getsize(cred_path) == 0:
        logger.warning(
            "[provider:claude_sdk] auth check: `claude` present but credentials "
            "file missing/empty — SDK transport not logged in",
        )
        return False, (
            "Claude CLI is installed but not logged in (SDK transport "
            "won't work either). Run `docker exec -it lazyclaw claude "
            "auth login` or `claude auth login` on the host."
        )

    # File present != token usable. An expired token with no refresh
    # token 401s every call, so check it here rather than letting the
    # whole process discover it one failed request at a time.
    try:
        with open(cred_path, encoding="utf-8") as fh:
            cred = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning(
            "[provider:claude_sdk] auth check: credentials file unreadable "
            "(%s) — trusting it rather than blocking boot", exc,
        )
        return True, ""

    ok, problem = _classify_oauth_credentials(cred, int(time.time() * 1000))
    if not ok:
        logger.error("[provider:claude_sdk] auth check: %s", problem)
        return False, problem

    logger.debug("[provider:claude_sdk] auth check: ready")
    return True, ""
