"""Immutable data models for the unified comms layer."""
from __future__ import annotations

from dataclasses import dataclass

# Inbox-facing channels — those the ChannelGateway can actually READ/SEND via
# a per-channel MCP tool (see gateway._DISPATCH). Telegram is intentionally
# EXCLUDED: it's the control channel (native adapter, no MCP read tool), so a
# telegram thread could never be opened — advertising it produced empty
# "No messages yet" opens. This does NOT affect telegram notifications
# (notifications/channel.py) or contact naming (contact_naming._HANDLE_KEYS).
VALID_COMMS_CHANNELS = ("whatsapp", "email", "instagram")


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
    """One message in a thread (from a live channel read).

    ``id`` and ``media`` are set when the channel read surfaces them
    (WhatsApp: message id + media metadata dict from describeMedia —
    ``{kind, mimetype, file_name, seconds, voice_note, size_bytes}``).
    ``media`` lets the inbox render a playable voice note / photo instead
    of a "[audio]" placeholder; ``id`` is what the per-channel download
    tool needs to fetch the actual bytes.
    """
    sender: str
    text: str
    timestamp: str
    is_mine: bool = False
    id: str | None = None
    media: dict | None = None


@dataclass(frozen=True)
class Contact:
    name: str
    handle: str


@dataclass(frozen=True)
class SendResult:
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class ReadResult:
    """Outcome of a live thread read — distinguishes a genuinely-empty
    thread (``ok=True, messages=()``) from a FAILED channel read
    (``ok=False, error=...``). Mirrors :class:`SendResult` so callers never
    have to guess whether ``[]`` means "no messages" or "the MCP is down"
    (the silent-empty antipattern)."""
    ok: bool
    messages: tuple[Msg, ...] = ()
    error: str | None = None
