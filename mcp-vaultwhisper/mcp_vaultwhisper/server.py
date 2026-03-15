"""MCP server — exposes 5 VaultWhisper tools."""
from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.types import Tool, TextContent

from mcp_vaultwhisper.config import VaultWhisperConfig
from mcp_vaultwhisper.patterns import PIIPattern
from mcp_vaultwhisper.scrubber import scrub
from mcp_vaultwhisper.restorer import restore
from mcp_vaultwhisper.detector import detect_pii
from mcp_vaultwhisper.proxy import proxy_chat

logger = logging.getLogger(__name__)


def _redact_value(value: str) -> str:
    """Mask PII value for safe display: show first/last char only."""
    if len(value) <= 3:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]


def create_server(
    config: VaultWhisperConfig,
    patterns: tuple[PIIPattern, ...],
) -> Server:
    """Create the MCP server with 5 VaultWhisper tools."""
    server = Server("mcp-vaultwhisper")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="vaultwhisper_scrub",
                description="Strip PII from text, returning scrubbed text and a mapping for later restoration.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to scrub PII from"},
                    },
                    "required": ["text"],
                },
            ),
            Tool(
                name="vaultwhisper_restore",
                description="Re-inject original PII values into text using a placeholder mapping.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text with placeholders"},
                        "mapping": {"type": "object", "description": "Placeholder-to-original mapping from scrub"},
                    },
                    "required": ["text", "mapping"],
                },
            ),
            Tool(
                name="vaultwhisper_chat",
                description="Privacy-safe AI chat: scrubs PII from messages, sends to free AI, restores PII in response.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "messages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "role": {"type": "string", "enum": ["system", "user", "assistant"]},
                                    "content": {"type": "string"},
                                },
                                "required": ["role", "content"],
                            },
                            "description": "Chat messages in OpenAI format",
                        },
                        "system": {"type": "string", "description": "Optional system prompt"},
                    },
                    "required": ["messages"],
                },
            ),
            Tool(
                name="vaultwhisper_detect",
                description="Detect PII in text without scrubbing (audit mode). Returns redacted detections.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to scan for PII"},
                    },
                    "required": ["text"],
                },
            ),
            Tool(
                name="vaultwhisper_patterns",
                description="List all active PII detection patterns with descriptions.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list"], "description": "Action to perform"},
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "vaultwhisper_scrub":
            text = arguments.get("text", "")
            result = scrub(text, patterns)
            return [TextContent(type="text", text=json.dumps({
                "scrubbed_text": result.scrubbed_text,
                "mapping": result.mapping,
                "detection_count": result.detection_count,
            }, indent=2))]

        elif name == "vaultwhisper_restore":
            text = arguments.get("text", "")
            mapping = arguments.get("mapping", {})
            restored = restore(text, mapping)
            return [TextContent(type="text", text=restored)]

        elif name == "vaultwhisper_chat":
            return await _handle_chat(config, patterns, arguments)

        elif name == "vaultwhisper_detect":
            text = arguments.get("text", "")
            detections = detect_pii(text, patterns)
            items = [
                {
                    "type": d.pii_type.value,
                    "value": _redact_value(d.value),
                    "start": d.start,
                    "end": d.end,
                    "placeholder": d.placeholder,
                }
                for d in detections
            ]
            return [TextContent(type="text", text=json.dumps({
                "detections": items,
                "count": len(items),
            }, indent=2))]

        elif name == "vaultwhisper_patterns":
            items = [
                {
                    "type": p.pii_type.value,
                    "regex": p.regex,
                    "description": p.description,
                    "sensitivity": p.sensitivity,
                }
                for p in patterns
            ]
            return [TextContent(type="text", text=json.dumps({
                "mode": config.mode,
                "patterns": items,
                "count": len(items),
            }, indent=2))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def _handle_chat(
    config: VaultWhisperConfig,
    patterns: tuple[PIIPattern, ...],
    arguments: dict,
) -> list[TextContent]:
    """Handle vaultwhisper_chat: scrub → AI → restore."""
    messages = arguments.get("messages", [])
    system = arguments.get("system")

    if system:
        messages = [{"role": "system", "content": system}] + list(messages)

    combined_mapping: dict[str, str] = {}
    scrubbed_messages: list[dict] = []
    for msg in messages:
        result = scrub(msg.get("content", ""), patterns)
        combined_mapping.update(result.mapping)
        scrubbed_messages.append({"role": msg["role"], "content": result.scrubbed_text})

    ai_result = await proxy_chat(config, scrubbed_messages)
    if "error" in ai_result:
        return [TextContent(type="text", text=f"Error: {ai_result['error']}")]

    restored_content = restore(ai_result["content"], combined_mapping)
    provider_tag = f"[{ai_result['provider']}/{ai_result['model']}]"
    return [TextContent(type="text", text=f"{provider_tag} {restored_content}")]
