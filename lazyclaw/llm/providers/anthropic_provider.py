from __future__ import annotations

import json
import logging
import re
import uuid

import anthropic

from lazyclaw.llm.providers.base import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall

_log = logging.getLogger(__name__)


def _looks_like_json_plan(text: str) -> bool:
    """True when a text-only reply is machine-plan narration, not an answer.

    MiniMax M3/M2.7 under pressure emit fenced ```json {goal, steps} plans
    as prose instead of tool_use (2026-07-02 Upwork bail). A fenced json
    block or a bare JSON document is never a legitimate user-facing reply.
    """
    t = text.strip()
    if not t:
        return False
    if t.startswith("```json"):
        return True
    if t[0] in "{[":
        try:
            json.loads(t)
            return True
        except (json.JSONDecodeError, ValueError):
            return False
    return False

# ─── MiniMax M2 / M2.7 tool-call recovery ────────────────────────────────────
#
# M2 is hardcoded to emit tool calls in its proprietary XML format:
#
#   <minimax:tool_call>
#   <invoke name="tool_name">
#   <parameter name="key">value</parameter>
#   </invoke>
#   </minimax:tool_call>
#
# MiniMax's `/anthropic` endpoint usually translates this to native Anthropic
# `tool_use` content blocks server-side, but raw markup leaks through under
# interleaved-thinking + multi-tool prompts. The parser below recovers it.
#
# Vendored verbatim (with adapter glue) from MiniMax's own tool calling guide:
#   https://huggingface.co/MiniMaxAI/MiniMax-M2/blob/main/docs/tool_calling_guide.md
# License: MIT via the MiniMax-M2 model card. Battle-tested by the vLLM and
# SGLang plugins. Schema-driven type coercion (int/float/bool/array/object)
# beats heuristic guessing.
#
# IMPORTANT: This pass strips `<minimax:tool_call>` blocks but MUST NOT touch
# `<think>...</think>` content. M2 is an interleaved-thinking model — per the
# HF model card, retaining thinking content in assistant history is required
# for multi-turn quality. The strip regex below is wrapper-scoped on purpose.

_MINIMAX_TOOL_CALL_BLOCK_RE = re.compile(
    r"<minimax:tool_call>(.*?)</minimax:tool_call>", re.DOTALL
)
_MINIMAX_INVOKE_RE = re.compile(r"<invoke name=(.*?)</invoke>", re.DOTALL)
_MINIMAX_PARAMETER_RE = re.compile(r"<parameter name=(.*?)</parameter>", re.DOTALL)


def _minimax_extract_name(name_str: str) -> str:
    """Strip surrounding quotes from a name token. (Vendored from MiniMax guide.)"""
    name_str = name_str.strip()
    if name_str.startswith('"') and name_str.endswith('"'):
        return name_str[1:-1]
    if name_str.startswith("'") and name_str.endswith("'"):
        return name_str[1:-1]
    return name_str


def _minimax_convert_param_value(value: str, param_type: str) -> object:
    """Coerce a parameter string to its declared type. (Vendored from MiniMax guide.)"""
    if value.lower() == "null":
        return None

    param_type = param_type.lower()
    if param_type in ("string", "str", "text"):
        return value
    if param_type in ("integer", "int"):
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    if param_type in ("number", "float"):
        try:
            val = float(value)
            return val if val != int(val) else int(val)
        except (ValueError, TypeError):
            return value
    if param_type in ("boolean", "bool"):
        return value.lower() in ("true", "1")
    if param_type in ("object", "array"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    # Unknown declared type — try JSON, fall back to raw string.
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _minimax_parse_tool_calls(
    model_output: str, tools: list[dict] | None = None,
) -> list[dict]:
    """Parse MiniMax XML markup into [{name, arguments}, ...].

    Vendored verbatim from MiniMax-M2's tool calling guide. Schema-aware: when
    `tools` is provided in OpenAI or MiniMax shape, parameter values are coerced
    to their declared type.
    """
    if "<minimax:tool_call>" not in model_output:
        return []

    parsed: list[dict] = []
    try:
        for tool_call_match in _MINIMAX_TOOL_CALL_BLOCK_RE.findall(model_output):
            for invoke_match in _MINIMAX_INVOKE_RE.findall(tool_call_match):
                name_match = re.search(r"^([^>]+)", invoke_match)
                if not name_match:
                    continue
                function_name = _minimax_extract_name(name_match.group(1))

                # Resolve parameter type config from tool definitions, if any.
                param_config: dict = {}
                if tools:
                    for tool in tools:
                        tool_name = (
                            tool.get("name")
                            or tool.get("function", {}).get("name")
                        )
                        if tool_name == function_name:
                            params = (
                                tool.get("parameters")
                                or tool.get("function", {}).get("parameters")
                                or tool.get("input_schema")
                            )
                            if isinstance(params, dict) and "properties" in params:
                                param_config = params["properties"]
                            break

                # Walk <parameter name="K">V</parameter> children.
                args: dict = {}
                for param_match in _MINIMAX_PARAMETER_RE.findall(invoke_match):
                    body_match = re.search(r"^([^>]+)>(.*)", param_match, re.DOTALL)
                    if not body_match:
                        continue
                    param_name = _minimax_extract_name(body_match.group(1))
                    param_value = body_match.group(2).strip()
                    if param_value.startswith("\n"):
                        param_value = param_value[1:]
                    if param_value.endswith("\n"):
                        param_value = param_value[:-1]

                    declared_type = "string"
                    cfg = param_config.get(param_name)
                    if isinstance(cfg, dict) and "type" in cfg:
                        declared_type = cfg["type"]
                    args[param_name] = _minimax_convert_param_value(
                        param_value, declared_type,
                    )

                parsed.append({"name": function_name, "arguments": args})
    except Exception:  # noqa: BLE001 — match upstream behavior
        _log.exception("Failed to parse MiniMax tool calls")
        return []

    return parsed


def _extract_minimax_tool_calls(
    text: str, tools: list[dict] | None = None,
) -> tuple[str, list[ToolCall]]:
    """Recover MiniMax-style tool-call markup from text content.

    Returns (cleaned_text, tool_calls). Substring-gated — no-op for real
    Anthropic responses. `<think>...</think>` content is preserved intact;
    only the `<minimax:tool_call>` wrapper is stripped.
    """
    if not text or "<minimax:tool_call>" not in text:
        return text, []

    parsed = _minimax_parse_tool_calls(text, tools)
    cleaned = _MINIMAX_TOOL_CALL_BLOCK_RE.sub("", text).strip()

    if not parsed:
        # Markup was present but unparseable — leave text alone so a human can
        # see what M2 emitted, return no calls.
        return text, []

    calls = [
        ToolCall(
            id=f"minimax_{uuid.uuid4().hex[:12]}",
            name=item["name"],
            arguments=item["arguments"],
        )
        for item in parsed
    ]
    _log.warning(
        "Recovered %d MiniMax-style tool call(s) from text content. "
        "Server-side adapter did not translate to native tool_use blocks.",
        len(calls),
    )
    return cleaned, calls


class AnthropicProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        *,
        disable_prompt_cache: bool = False,
        default_model: str = "claude-sonnet-4-6",
        default_tool_choice: dict | str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        # Per-request timeout. The anthropic SDK default is ~600s; for
        # MiniMax's compat endpoint we pass a tighter bound so a hung call
        # raises APITimeoutError quickly and the caller can escalate to the
        # fallback model instead of stalling for minutes. None → SDK default
        # (real Anthropic is reliable, so it stays unbounded).
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        # Cap SDK-level retries so a persistently-slow endpoint can't multiply
        # the wait (default max_retries=2 → up to 3× the timeout). None → SDK
        # default.
        if max_retries is not None:
            client_kwargs["max_retries"] = max_retries
        self._client = anthropic.AsyncAnthropic(**client_kwargs)
        self._disable_prompt_cache = disable_prompt_cache
        self._default_model = default_model
        # Provider-wide default applied when the caller doesn't override
        # tool_choice. Used to nudge MiniMax's Anthropic-compat adapter (which
        # silently defaults to `none` on some paths) into the documented
        # `auto` behaviour. No-op for real Anthropic — `auto` is already the
        # documented default.
        self._default_tool_choice = default_tool_choice
        # Per-instance counter for diagnostics: text-only MiniMax turns vs.
        # total MiniMax turns. Exposed via `text_only_stats()` for the
        # `/status` Telegram command.
        self._minimax_total_turns = 0
        self._minimax_text_only_turns = 0

    @staticmethod
    def _convert_tools(openai_tools: list[dict]) -> list[dict]:
        """Convert OpenAI function-calling tool format to Anthropic tool format."""
        anthropic_tools = []
        for tool in openai_tools:
            func = tool.get("function", {})
            anthropic_tools.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return anthropic_tools

    @staticmethod
    def _serialize_messages(messages: list[LLMMessage]) -> list[dict]:
        """Serialize messages for Anthropic API, handling tool calls and merging consecutive user messages.

        Ensures the conversation never ends with an assistant message
        (Anthropic rejects assistant-prefill on newer models).
        """
        result: list[dict] = []

        for m in messages:
            if m.role == "system":
                continue

            if m.role == "assistant" and m.tool_calls:
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                result.append({"role": "assistant", "content": blocks})

            elif m.role == "tool":
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                }
                # Merge consecutive tool results into one user message
                if result and result[-1]["role"] == "user" and isinstance(result[-1]["content"], list):
                    result[-1]["content"].append(tool_result_block)
                else:
                    result.append({"role": "user", "content": [tool_result_block]})

            else:
                result.append({"role": m.role, "content": m.content})

        # Anthropic requires the conversation to end with a user message.
        # If it ends with assistant, drop trailing assistant messages.
        while result and result[-1].get("role") == "assistant":
            result.pop()

        return result

    @staticmethod
    def _with_cache(system_parts: list[str], tools: list[dict] | None) -> tuple:
        """Add cache_control breakpoints to system prompt and tools.

        Anthropic prompt caching: static content (system + tools) is cached
        across iterations in the agentic loop. First call pays 25% write
        surcharge, subsequent calls get 90% discount on cached tokens.
        """
        # System: list of text blocks, cache_control on the last one
        system = None
        if system_parts:
            blocks = [{"type": "text", "text": part} for part in system_parts]
            blocks[-1]["cache_control"] = {"type": "ephemeral"}
            system = blocks

        # Tools: cache_control on the last tool definition
        cached_tools = None
        if tools:
            cached_tools = list(tools)  # shallow copy
            cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}

        return system, cached_tools

    @staticmethod
    def _plain_system_and_tools(
        system_parts: list[str], tools: list[dict] | None,
    ) -> tuple:
        """Build system/tools payloads without any cache_control breakpoints.

        Used when talking to Anthropic-compatible endpoints (e.g. MiniMax) that
        don't implement prompt caching and reject unknown fields.
        """
        system = "\n\n".join(p for p in system_parts if p) if system_parts else None
        return system or None, tools

    async def chat(self, messages: list[LLMMessage], model: str, **kwargs) -> LLMResponse:
        system_parts = [m.content for m in messages if m.role == "system"]

        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        # Apply provider default when the caller didn't override AND tools
        # were actually attached. tool_choice without tools is a 400 on the
        # Anthropic API.
        if tool_choice is None and tools and self._default_tool_choice is not None:
            tool_choice = self._default_tool_choice

        if not model:
            model = self._default_model

        create_kwargs: dict = {
            "model": model,
            "messages": self._serialize_messages(messages),
            "max_tokens": kwargs.pop("max_tokens", 4096),
            **kwargs,
        }

        converted_tools = self._convert_tools(tools) if tools else None
        if self._disable_prompt_cache:
            system_payload, tools_payload = self._plain_system_and_tools(
                system_parts, converted_tools,
            )
        else:
            system_payload, tools_payload = self._with_cache(system_parts, converted_tools)
        if system_payload:
            create_kwargs["system"] = system_payload
        if tools_payload:
            create_kwargs["tools"] = tools_payload
        if tool_choice is not None:
            create_kwargs["tool_choice"] = tool_choice

        # Bounded retry loop: at most one extra request, fired only when
        # MiniMax narrates a fenced/bare JSON plan as prose instead of
        # emitting tool_use (2026-07-02 incident — see _looks_like_json_plan).
        # The retry re-issues the same create_kwargs with tool_choice forced
        # to {"type": "any"}; its response replaces the first unconditionally,
        # even if still text-only — never loop past one retry.
        # Usage accumulators: when the retry fires, TWO billed API calls
        # happen. MiniMax Token Plan is request/token-limited, so cost/rate
        # telemetry needs BOTH calls' usage, not just the last one. Summed
        # across every loop iteration below; when no retry fires this is
        # numerically identical to reading `response.usage` once (today's
        # behavior).
        _usage_input_tokens = 0
        _usage_output_tokens = 0
        _usage_cache_created = 0
        _usage_cache_read = 0
        _any_usage_seen = False

        _plan_retry_done = False
        while True:
            response = await self._client.messages.create(**create_kwargs)

            if response.usage:
                _any_usage_seen = True
                _usage_input_tokens += response.usage.input_tokens
                _usage_output_tokens += response.usage.output_tokens
                _usage_cache_created += (
                    getattr(response.usage, "cache_creation_input_tokens", 0) or 0
                )
                _usage_cache_read += (
                    getattr(response.usage, "cache_read_input_tokens", 0) or 0
                )

            text_parts: list[str] = []
            parsed_tool_calls: list[ToolCall] = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    parsed_tool_calls.append(
                        ToolCall(id=block.id, name=block.name, arguments=block.input)
                    )

            # Recover MiniMax-style tool markup if the server returned it as
            # text instead of native tool_use blocks.
            joined_text = "\n".join(text_parts)
            cleaned_text, recovered_calls = _extract_minimax_tool_calls(
                joined_text, converted_tools,
            )
            if recovered_calls:
                parsed_tool_calls.extend(recovered_calls)
                joined_text = cleaned_text

            # Diagnostic: text-only is a DEFECT only when tools were ATTACHED
            # but no tool_use came back — that's the "M3 narrated instead of
            # calling" signal. A tools=None request (the plan-gate round-0
            # plan DRAFT, or a pure-chat turn) returning text is CORRECT and
            # must NOT warn or inflate the drift counter — that false
            # positive sent the 2026-06-23 MiniMax investigation down a wrong
            # path ("M3 won't dispatch" on calls that were never meant to).
            # `tools_payload` is truthy iff tools were actually sent this
            # request. The total-turn counter still tracks every MiniMax call
            # (including the retry) so /status keeps a true denominator.
            if response.model and "MiniMax" in response.model:
                self._minimax_total_turns += 1
                if not parsed_tool_calls and joined_text:
                    if tools_payload:
                        self._minimax_text_only_turns += 1
                        _log.warning(
                            "MiniMax %s returned text-only despite %d tool(s) "
                            "attached (no tool_use): %r",
                            response.model, len(tools_payload), joined_text[:400],
                        )
                    else:
                        _log.debug(
                            "MiniMax %s returned text-only (no tools attached — "
                            "expected for chat/plan-draft): %r",
                            response.model, joined_text[:200],
                        )

            if (
                response.model and "MiniMax" in response.model
                and not parsed_tool_calls and joined_text and tools_payload
                and not _plan_retry_done
                and _looks_like_json_plan(joined_text)
            ):
                _plan_retry_done = True
                _log.warning(
                    "MiniMax %s narrated a JSON plan instead of tool_use — "
                    "retrying once with tool_choice={'type': 'any'}",
                    response.model,
                )
                create_kwargs["tool_choice"] = {"type": "any"}
                continue
            break

        usage = None
        if _any_usage_seen:
            usage = {
                "prompt_tokens": _usage_input_tokens,
                "completion_tokens": _usage_output_tokens,
                "total_tokens": _usage_input_tokens + _usage_output_tokens,
                "input_tokens": _usage_input_tokens,
                "output_tokens": _usage_output_tokens,
                "cache_creation_input_tokens": _usage_cache_created,
                "cache_read_input_tokens": _usage_cache_read,
            }

        return LLMResponse(
            content=joined_text,
            model=response.model,
            usage=usage,
            tool_calls=parsed_tool_calls or None,
        )

    async def stream_chat(self, messages: list[LLMMessage], model: str, **kwargs):
        """Stream chat responses from Anthropic."""
        from lazyclaw.llm.providers.base import StreamChunk

        system_parts = [m.content for m in messages if m.role == "system"]
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        # Mirror chat(): apply provider default when caller omitted tool_choice
        # AND tools are attached.
        if tool_choice is None and tools and self._default_tool_choice is not None:
            tool_choice = self._default_tool_choice

        if not model:
            model = self._default_model

        create_kwargs: dict = {
            "model": model,
            "messages": self._serialize_messages(messages),
            "max_tokens": kwargs.pop("max_tokens", 4096),
            **kwargs,
        }

        converted_tools = self._convert_tools(tools) if tools else None
        if self._disable_prompt_cache:
            system_payload, tools_payload = self._plain_system_and_tools(
                system_parts, converted_tools,
            )
        else:
            system_payload, tools_payload = self._with_cache(system_parts, converted_tools)
        if system_payload:
            create_kwargs["system"] = system_payload
        if tools_payload:
            create_kwargs["tools"] = tools_payload
        if tool_choice is not None:
            create_kwargs["tool_choice"] = tool_choice

        async with self._client.messages.stream(**create_kwargs) as stream:
            collected_text = ""
            collected_tool_calls: list[ToolCall] = []
            current_tool: dict | None = None
            # Track input usage from message_start (has cache stats)
            _input_usage: dict = {}
            # Anthropic's protocol emits one ``message_delta`` per stream, but a
            # MiniMax compat adapter (or a buggy server) emitting two would
            # double-count the diagnostics counters AND yield two terminal
            # done=True chunks — consumers treat the FIRST done as end-of-stream.
            # This once-guard makes the terminal yield + counter pump fire
            # exactly once per stream.
            _emitted_done = False

            async for event in stream:
                if event.type == "message_start":
                    # Capture input tokens + cache stats (only in message_start)
                    msg_usage = getattr(event.message, "usage", None)
                    if msg_usage:
                        _input_usage = {
                            "input_tokens": getattr(msg_usage, "input_tokens", 0),
                            "cache_creation_input_tokens": getattr(msg_usage, "cache_creation_input_tokens", 0) or 0,
                            "cache_read_input_tokens": getattr(msg_usage, "cache_read_input_tokens", 0) or 0,
                        }

                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool = {
                            "id": block.id,
                            "name": block.name,
                            "arguments": "",
                        }

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        collected_text += delta.text
                        yield StreamChunk(delta=delta.text, model=model)
                    elif delta.type == "input_json_delta" and current_tool:
                        current_tool["arguments"] += delta.partial_json

                elif event.type == "content_block_stop":
                    if current_tool:
                        import json as _json
                        try:
                            parsed_args = (
                                _json.loads(current_tool["arguments"])
                                if current_tool["arguments"]
                                else {}
                            )
                        except _json.JSONDecodeError:
                            parsed_args = {}
                        collected_tool_calls.append(
                            ToolCall(
                                id=current_tool["id"],
                                name=current_tool["name"],
                                arguments=parsed_args,
                            )
                        )
                        current_tool = None

                elif event.type == "message_delta":
                    if _emitted_done:
                        # A duplicate terminal event — ignore so counters and
                        # the done=True chunk fire exactly once.
                        continue
                    _emitted_done = True
                    usage = None
                    if hasattr(event, "usage") and event.usage:
                        # message_delta has output_tokens; input comes from message_start
                        input_t = _input_usage.get("input_tokens", 0)
                        output_t = getattr(event.usage, "output_tokens", 0)
                        usage = {
                            "prompt_tokens": input_t,
                            "completion_tokens": output_t,
                            "total_tokens": input_t + output_t,
                            "input_tokens": input_t,
                            "output_tokens": output_t,
                            "cache_creation_input_tokens": _input_usage.get("cache_creation_input_tokens", 0),
                            "cache_read_input_tokens": _input_usage.get("cache_read_input_tokens", 0),
                        }
                    # Recover MiniMax-style tool markup from streamed text when
                    # the server returned it as content instead of tool_use.
                    if not collected_tool_calls:
                        _, recovered = _extract_minimax_tool_calls(
                            collected_text, converted_tools,
                        )
                        if recovered:
                            collected_tool_calls.extend(recovered)
                    # Counter pump (mirrors chat()): used by /status to track
                    # how often M2.7 narrates instead of dispatching. Only
                    # MiniMax models contribute. We can't read the response
                    # `model` field on the stream object the same way as in
                    # chat(), so default to checking the requested model.
                    if model and "minimax" in model.lower():
                        self._minimax_total_turns += 1
                        # Only a text-only stream WITH tools attached is the
                        # narration defect; a tools=None chat stream returning
                        # text is expected (see chat() above).
                        if (
                            not collected_tool_calls
                            and collected_text
                            and tools_payload
                        ):
                            self._minimax_text_only_turns += 1
                    yield StreamChunk(
                        delta="",
                        tool_calls=collected_tool_calls or None,
                        usage=usage,
                        model=model,
                        done=True,
                    )

    def text_only_stats(self) -> dict:
        """Counters for MiniMax text-only turns vs. total MiniMax turns.

        Used by the `/status` Telegram command to surface drift over the
        lifetime of the provider. Both numbers are 0 when MiniMax has never
        been called through this provider instance.
        """
        return {
            "minimax_total_turns": self._minimax_total_turns,
            "minimax_text_only_turns": self._minimax_text_only_turns,
        }

    async def verify_key(self) -> bool:
        try:
            await self._client.messages.create(
                model=self._default_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            return True
        except anthropic.AuthenticationError:
            return False
