"""Structured tool results with optional binary attachments.

Skills can return either a plain ``str`` (backward-compatible) or a
``ToolResult`` carrying text *and* attachments (images, files).
Channels inspect the attachments and deliver them natively — e.g.
Telegram sends photos via ``send_photo``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Attachment:
    """A single binary attachment produced by a tool."""

    data: bytes
    media_type: str  # e.g. "image/png", "image/jpeg", "application/pdf"
    filename: str = ""


@dataclass(frozen=True)
class ToolResult:
    """Rich tool result: text for the LLM + optional attachments for channels."""

    text: str
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)
