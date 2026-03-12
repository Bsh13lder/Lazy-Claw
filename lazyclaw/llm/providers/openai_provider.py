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

    async def verify_key(self) -> bool:
        try:
            await self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            return True
        except openai.AuthenticationError:
            return False
