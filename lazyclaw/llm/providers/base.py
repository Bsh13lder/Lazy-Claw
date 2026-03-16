from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMMessage:
    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = field(default=None)


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict | None = None
    tool_calls: list[ToolCall] | None = field(default=None)


@dataclass(frozen=True)
class StreamChunk:
    """A single chunk from a streaming LLM response."""
    delta: str = ""
    tool_calls: list[ToolCall] | None = None
    usage: dict | None = None
    model: str = ""
    done: bool = False


class BaseLLMProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[LLMMessage], model: str, **kwargs) -> LLMResponse:
        ...

    @abstractmethod
    async def verify_key(self) -> bool:
        ...

    async def stream_chat(
        self, messages: list[LLMMessage], model: str, **kwargs
    ):
        """Stream chat responses. Default: falls back to non-streaming.

        Yields StreamChunk instances. Override in providers that support streaming.
        """
        from lazyclaw.llm.providers.base import StreamChunk
        response = await self.chat(messages, model, **kwargs)
        yield StreamChunk(
            delta=response.content,
            tool_calls=response.tool_calls,
            usage=response.usage,
            model=response.model,
            done=True,
        )
