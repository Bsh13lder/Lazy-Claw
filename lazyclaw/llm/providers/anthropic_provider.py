from __future__ import annotations

import anthropic

from lazyclaw.llm.providers.base import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall


class AnthropicProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        *,
        disable_prompt_cache: bool = False,
        default_model: str = "claude-sonnet-4-6",
    ) -> None:
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**client_kwargs)
        self._disable_prompt_cache = disable_prompt_cache
        self._default_model = default_model

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

        response = await self._client.messages.create(**create_kwargs)

        text_parts: list[str] = []
        parsed_tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                parsed_tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input)
                )

        usage = None
        if response.usage:
            input_t = response.usage.input_tokens
            output_t = response.usage.output_tokens
            cache_created = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            usage = {
                "prompt_tokens": input_t,
                "completion_tokens": output_t,
                "total_tokens": input_t + output_t,
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cache_creation_input_tokens": cache_created,
                "cache_read_input_tokens": cache_read,
            }

        return LLMResponse(
            content="\n".join(text_parts),
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
                    yield StreamChunk(
                        delta="",
                        tool_calls=collected_tool_calls or None,
                        usage=usage,
                        model=model,
                        done=True,
                    )

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
