from __future__ import annotations

import json

import openai

from lazyclaw.llm.providers.base import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)

    @staticmethod
    def _serialize_message(m: LLMMessage) -> dict:
        if m.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": m.tool_call_id,
                "content": m.content,
            }
        if m.role == "assistant" and m.tool_calls:
            return {
                "role": "assistant",
                "content": m.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ],
            }
        return {"role": m.role, "content": m.content}

    async def chat(self, messages: list[LLMMessage], model: str, **kwargs) -> LLMResponse:
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)

        create_kwargs: dict = {
            "model": model,
            "messages": [self._serialize_message(m) for m in messages],
            **kwargs,
        }
        if tools:
            create_kwargs["tools"] = tools
        if tool_choice is not None:
            create_kwargs["tool_choice"] = tool_choice

        response = await self._client.chat.completions.create(**create_kwargs)
        choice = response.choices[0]

        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        parsed_tool_calls = None
        if choice.message.tool_calls:
            parsed_tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                )
                for tc in choice.message.tool_calls
            ]

        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            usage=usage,
            tool_calls=parsed_tool_calls,
        )

    async def stream_chat(self, messages: list[LLMMessage], model: str, **kwargs):
        """Stream chat responses from OpenAI."""
        from lazyclaw.llm.providers.base import StreamChunk

        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)

        create_kwargs: dict = {
            "model": model,
            "messages": [self._serialize_message(m) for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            **kwargs,
        }
        if tools:
            create_kwargs["tools"] = tools
        if tool_choice is not None:
            create_kwargs["tool_choice"] = tool_choice

        response = await self._client.chat.completions.create(**create_kwargs)

        collected_content = ""
        collected_tool_calls: dict[int, dict] = {}
        response_model = model
        usage = None

        async for chunk in response:
            if not chunk.choices and hasattr(chunk, "usage") and chunk.usage:
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }
                continue

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            response_model = chunk.model or model

            # Text content
            if delta.content:
                collected_content += delta.content
                yield StreamChunk(delta=delta.content, model=response_model)

            # Tool calls
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in collected_tool_calls:
                        collected_tool_calls[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    entry = collected_tool_calls[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

            # Check for finish — save tool calls but DON'T return yet.
            # Usage chunk arrives AFTER finish_reason in OpenAI streaming.
            if chunk.choices[0].finish_reason is not None:
                parsed_tcs = None
                if collected_tool_calls:
                    parsed_tcs = [
                        ToolCall(
                            id=tc["id"],
                            name=tc["name"],
                            arguments=json.loads(tc["arguments"]) if tc["arguments"] else {},
                        )
                        for tc in collected_tool_calls.values()
                    ]

                # Continue reading to capture the usage chunk that follows
                async for tail_chunk in response:
                    if hasattr(tail_chunk, "usage") and tail_chunk.usage:
                        usage = {
                            "prompt_tokens": tail_chunk.usage.prompt_tokens,
                            "completion_tokens": tail_chunk.usage.completion_tokens,
                            "total_tokens": tail_chunk.usage.total_tokens,
                        }
                        break

                yield StreamChunk(
                    delta="",
                    tool_calls=parsed_tcs,
                    usage=usage,
                    model=response_model,
                    done=True,
                )
                return

        # Safety: if we exit the loop without a finish_reason
        parsed_tcs = None
        if collected_tool_calls:
            parsed_tcs = [
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=json.loads(tc["arguments"]) if tc["arguments"] else {},
                )
                for tc in collected_tool_calls.values()
            ]
        yield StreamChunk(
            delta="",
            tool_calls=parsed_tcs,
            usage=usage,
            model=response_model,
            done=True,
        )

    async def verify_key(self) -> bool:
        try:
            await self._client.chat.completions.create(
                model="gpt-5-mini",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            return True
        except openai.AuthenticationError:
            return False
