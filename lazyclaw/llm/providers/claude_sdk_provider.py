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
import uuid
from typing import Any

from lazyclaw.llm.providers.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    StreamChunk,
    ToolCall,
)

logger = logging.getLogger(__name__)


# Built-in Claude Code tools and MCP defaults that must always be blocked.
# `strict_mcp_config=True` locks external MCPs out, but Claude Code's
# *native* tool list is a separate Anthropic surface — it has to be
# enumerated here. Tools observed leaking past strict_mcp_config in live
# logs on 2026-05-14: `Skill`, `run_command` (with `RunCommand` camel
# variant added defensively). When Sonnet/Opus emits one of these,
# lazyclaw's runtime drops it as a hallucinated tool — wasted slot in
# the batch + a confused brain.
_DISALLOWED_BUILT_INS = [
    "Bash", "Read", "Edit", "Write", "Glob", "Grep",
    "WebSearch", "WebFetch", "Task", "Agent",
    "TodoWrite", "NotebookEdit", "BashOutput", "KillShell",
    "ToolSearch",
    # Added 2026-05-14 after live leak observations:
    "Skill",                 # Claude Code's skill-runner — clashes with lazyclaw's `search_tools`
    "run_command", "RunCommand",  # Claude Code's bash equivalent
    "SlashCommand",          # Claude Code's slash-command invoker
    "Skills",                # Plural variant occasionally emitted
]

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


def _is_known_registry_tool(
    name: str,
    name_map: dict[str, str] | None,
) -> bool:
    """True if `name` resolves to a registered lazyclaw skill.

    The brain occasionally invents tool names that aren't in
    `_DISALLOWED_BUILT_INS` AND aren't in the skill registry — usually
    a mis-spelled skill or a Claude Code built-in we haven't blocked
    yet. Surfacing them at WARNING means new leaks show up in logs the
    day they happen instead of whenever a user notices a flow stalled.

    `name_map` maps short → registry name. After `_unmap_tool_name`
    runs, a known tool will either be a value in the map (resolved) or
    a key (passthrough — short==registry).
    """
    if not name_map:
        return False
    if name in name_map.values():
        return True
    if name in name_map:
        return True
    return False


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
    deduped: list[ToolCall] = []
    for tc in tool_calls:
        # Pass 1: read-only listing — collapse by name regardless of args
        if _is_read_only_list_tool(tc.name):
            if tc.name in seen_listing_names:
                continue
            seen_listing_names.add(tc.name)
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
        self._hallucinated_rescue_count: int = 0

    async def verify_key(self) -> bool:
        """Health check — confirm SDK importable and binary findable.

        We don't actually spin a query here (would consume subscription
        for a trivial probe); just verify the prerequisites.
        """
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return False
        return self._claude_bin is not None

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
        prompt_text = _serialize_messages(messages)
        options, name_map = self._build_options(tools_spec, create_sdk_mcp_server)

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
        self._hallucinated_rescue_count = 0

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
                            if not _is_known_registry_tool(registry_name, name_map):
                                # Not blocked, not registered — usually a
                                # mis-spelled skill or a fresh Claude Code
                                # built-in. Surface at WARNING so new leaks
                                # are visible the day they happen.
                                logger.warning(
                                    "Claude SDK: unknown tool name %r "
                                    "(resolved %r) — not in blocklist, not "
                                    "a registered skill. Dropping. Add to "
                                    "_DISALLOWED_BUILT_INS if this is a "
                                    "Claude Code built-in.",
                                    block.name, registry_name,
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
                    if msg.total_cost_usd is None:
                        logger.debug(
                            "Claude SDK: cost telemetry missing from "
                            "ResultMessage (session=%s) — cost reported as $0",
                            getattr(msg, "session_id", "?"),
                        )
                    api_equiv_cost = float(msg.total_cost_usd or 0.0)
                    stop_reason = msg.stop_reason
                    session_id = msg.session_id
                    if msg.is_error:
                        # SDK signals an upstream error — surface it as
                        # a clear runtime failure (NOT SDKUnavailable, so
                        # eco_router doesn't silently fall back over a
                        # legitimate API error).
                        errs = msg.errors or [msg.result or "unknown"]
                        api_error = "; ".join(str(e) for e in errs)

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
            err_str = str(exc)
            looks_like_success_tail = (
                "returned an error result: success" in err_str
            )
            have_usable_response = bool(
                (text_parts or tool_calls) and not api_error
            )
            if looks_like_success_tail and have_usable_response:
                logger.warning(
                    "Claude SDK: swallowed trailing 'error result: success' "
                    "(turn succeeded — %d text chars + %d tool call(s))",
                    sum(len(t) for t in text_parts), len(tool_calls),
                )
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
        if self._hallucinated_rescue_count >= 3:
            # Three or more rescues in one turn = the brain is in a
            # broken pattern. Surface via usage so taor can inject a
            # nudge ("use the exact tool names provided") on the next
            # turn. The provider doesn't mutate the conversation
            # directly — that's the agent's job.
            logger.warning(
                "Claude SDK: %d hallucinated tool-name rescues this turn — "
                "next-turn nudge requested",
                self._hallucinated_rescue_count,
            )
            usage["hallucinated_rescues"] = self._hallucinated_rescue_count

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
        prompt_text = _serialize_messages(messages)
        options, name_map = self._build_options(tools_spec, create_sdk_mcp_server)

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
        self._hallucinated_rescue_count = 0

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
                            if not _is_known_registry_tool(registry_name, name_map):
                                logger.warning(
                                    "Claude SDK stream: unknown tool name "
                                    "%r (resolved %r) — not in blocklist, "
                                    "not a registered skill. Dropping. Add "
                                    "to _DISALLOWED_BUILT_INS if this is a "
                                    "Claude Code built-in.",
                                    block.name, registry_name,
                                )
                                continue
                            tool_calls.append(ToolCall(
                                id=block.id or f"sdk_{uuid.uuid4().hex[:8]}",
                                name=registry_name,
                                arguments=block.input or {},
                            ))
                elif isinstance(msg, ResultMessage):
                    usage_raw = msg.usage
                    if msg.total_cost_usd is None:
                        logger.debug(
                            "Claude SDK stream: cost telemetry missing from "
                            "ResultMessage (session=%s) — cost reported as $0",
                            getattr(msg, "session_id", "?"),
                        )
                    api_equiv_cost = float(msg.total_cost_usd or 0.0)
                    stop_reason = msg.stop_reason
                    session_id = msg.session_id
                    if msg.is_error:
                        errs = msg.errors or [msg.result or "unknown"]
                        api_error = "; ".join(str(e) for e in errs)
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
            # Same "error result: success" absorption guard as chat().
            err_str = str(exc)
            looks_like_success_tail = (
                "returned an error result: success" in err_str
            )
            have_usable_response = bool(
                (text_parts or tool_calls) and not api_error
            )
            if looks_like_success_tail and have_usable_response:
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
        if self._hallucinated_rescue_count >= 3:
            logger.warning(
                "Claude SDK stream: %d hallucinated tool-name rescues this "
                "turn — next-turn nudge requested",
                self._hallucinated_rescue_count,
            )
            usage["hallucinated_rescues"] = self._hallucinated_rescue_count

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
            self._hallucinated_rescue_count += 1
            logger.warning(
                "Claude SDK: rescued hallucinated MCP tool name %r "
                "(hybrid uuid/sdk form) → %r (#%d this call)",
                sdk_name, short, self._hallucinated_rescue_count,
            )
        elif _MCP_UUID_RE.match(short):
            short = _MCP_UUID_RE.sub("", short)
            self._hallucinated_rescue_count += 1
            logger.warning(
                "Claude SDK: rescued raw registry MCP tool name %r → %r "
                "(#%d this call)",
                sdk_name, short, self._hallucinated_rescue_count,
            )
        # Prefer caller-supplied per-call map (concurrent-safe); fall
        # back to self for legacy single-call usage.
        map_to_use = name_map if name_map is not None else self._tool_name_map
        return map_to_use.get(short, short)

    def _build_options(
        self,
        tools_spec: list[dict],
        create_sdk_mcp_server,
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
            # CRITICAL: without this the SDK inherits the host user's
            # global Claude Code MCP config (~/.claude/mcp_servers.json)
            # — meaning Sonnet sees and uses globally-registered MCPs
            # (Canva, Gmail, host Upwork, etc.) that lazyclaw's runtime
            # has NO knowledge of. Tool dispatch then crashes because
            # the agent can't execute a tool that isn't in its registry.
            # strict_mcp_config=True locks the surface down to ONLY the
            # MCPs we explicitly pass in (the lazyclaw in-process server).
            "strict_mcp_config": True,
        }
        if mcp_servers:
            kw["mcp_servers"] = mcp_servers
            kw["allowed_tools"] = allowed_tools
        if self._claude_bin:
            kw["cli_path"] = self._claude_bin

        # System prompt: brief, similar to the CLI provider's "you are
        # LazyClaw" preamble. System context blocks already arrive in the
        # serialized messages, so this is just an identity marker.
        kw["system_prompt"] = (
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
        return False, (
            "claude-agent-sdk is not installed. Run "
            "`pip install claude-agent-sdk>=0.1.81` or rebuild Docker."
        )

    if _find_claude_binary() is None:
        return True, ""  # SDK unused — silent.

    import sys
    if sys.platform == "darwin" and not os.path.exists("/.dockerenv"):
        return True, ""

    cred_path = os.path.expanduser("~/.claude/.credentials.json")
    if not os.path.isfile(cred_path) or os.path.getsize(cred_path) == 0:
        return False, (
            "Claude CLI is installed but not logged in (SDK transport "
            "won't work either). Run `docker exec -it lazyclaw claude "
            "/login` or `claude /login` on the host."
        )

    return True, ""
