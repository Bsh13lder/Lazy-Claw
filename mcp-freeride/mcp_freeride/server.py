from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.types import Tool, TextContent

from mcp_freeride.router import FreeRideRouter, AllProvidersFailedError

logger = logging.getLogger(__name__)


def create_server(router: FreeRideRouter) -> Server:
    server = Server("mcp-freeride")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="freeride_chat",
                description="Send a message to the best available free AI. Auto-routes with fallback.",
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
                        "model": {
                            "type": "string",
                            "description": "Optional model hint (e.g. 'groq/llama-3.3-70b-versatile')",
                        },
                        "system": {
                            "type": "string",
                            "description": "Optional system prompt (prepended to messages)",
                        },
                    },
                    "required": ["messages"],
                },
            ),
            Tool(
                name="freeride_models",
                description="List all available free AI models across all configured providers.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="freeride_status",
                description="Show health status of all configured AI providers.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "freeride_chat":
            messages = arguments["messages"]
            model = arguments.get("model")
            system = arguments.get("system")
            if system:
                messages = [{"role": "system", "content": system}] + messages
            try:
                result = await router.chat(messages, model)
                text = f"[{result['provider']}/{result['model']}] {result['content']}"
                return [TextContent(type="text", text=text)]
            except AllProvidersFailedError:
                return [TextContent(type="text", text="Error: All free AI providers failed. Check API keys and try again.")]

        elif name == "freeride_models":
            models = router.list_models()
            lines = [f"{'Provider':<15} {'Model':<45} {'Status':<10} {'Latency':>10}"]
            lines.append("-" * 85)
            for m in models:
                status = "alive" if m["healthy"] else "down"
                latency = f"{m['avg_latency_ms']:.0f}ms" if m["avg_latency_ms"] > 0 else "n/a"
                lines.append(f"{m['provider']:<15} {m['model']:<45} {status:<10} {latency:>10}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "freeride_status":
            status = router.get_status()
            return [TextContent(type="text", text=json.dumps(status, indent=2))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
