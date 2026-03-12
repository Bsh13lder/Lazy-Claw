from __future__ import annotations

import anthropic

from lazyclaw.llm.providers.base import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

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
        """Serialize messages for Anthropic API, handling tool calls and merging consecutive user messages."""
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

        return result

    async def chat(self, messages: list[LLMMessage], model: str, **kwargs) -> LLMResponse:
        system_parts = [m.content for m in messages if m.role == "system"]

        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)

        if not model:
            model = "claude-sonnet-4-20250514"

        create_kwargs: dict = {
            "model": model,
            "messages": self._serialize_messages(messages),
            "max_tokens": kwargs.pop("max_tokens", 4096),
            **kwargs,
        }
        if system_parts:
            create_kwargs["system"] = "\n\n".join(system_parts)
        if tools:
            create_kwargs["tools"] = self._convert_tools(tools)
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
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        return LLMResponse(
            content="\n".join(text_parts),
            model=response.model,
            usage=usage,
            tool_calls=parsed_tool_calls or None,
        )

    async def verify_key(self) -> bool:
        try:
            await self._client.messages.create(
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            return True
        except anthropic.AuthenticationError:
            return False
