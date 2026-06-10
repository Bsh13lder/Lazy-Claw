"""Immutable data models for the unified comms layer."""
from __future__ import annotations

from dataclasses import dataclass

VALID_COMMS_CHANNELS = ("whatsapp", "email", "instagram", "telegram")


@dataclass(frozen=True)
class ThreadRef:
    channel: str
    contact: str

    def as_dict(self) -> dict:
        return {"channel": self.channel, "contact": self.contact}


@dataclass(frozen=True)
class ChannelThread:
    id: str
    user_id: str
    channel: str
    contact_handle: str
    contact_name: str | None
    last_preview: str | None
    unread_count: int
    last_activity: str
    last_seen_msg_id: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "channel": self.channel,
            "contact_handle": self.contact_handle,
            "contact_name": self.contact_name,
            "last_preview": self.last_preview,
            "unread_count": self.unread_count,
            "last_activity": self.last_activity,
            "last_seen_msg_id": self.last_seen_msg_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
        }


@dataclass(frozen=True)
class Msg:
    """One message in a thread (from a live channel read)."""
    sender: str
    text: str
    timestamp: str
    is_mine: bool = False


@dataclass(frozen=True)
class Contact:
    name: str
    handle: str


@dataclass(frozen=True)
class SendResult:
    ok: bool
    error: str | None = None
