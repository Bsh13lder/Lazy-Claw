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


class BaseLLMProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[LLMMessage], model: str, **kwargs) -> LLMResponse:
        ...

    @abstractmethod
    async def verify_key(self) -> bool:
        ...
