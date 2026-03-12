from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class InboundMessage:
    channel: str
    external_user_id: str
    text: str
    metadata: dict | None = None


@dataclass
class OutboundMessage:
    text: str
    metadata: dict | None = None


class ChannelAdapter(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_message(
        self, external_user_id: str, message: OutboundMessage
    ) -> None: ...
