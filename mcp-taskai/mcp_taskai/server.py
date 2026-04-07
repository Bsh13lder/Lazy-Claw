from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.types import Tool, TextContent

from mcp_taskai.ai_client import AllProvidersFailedError
from mcp_taskai.intelligence import TaskIntelligence

logger = logging.getLogger(__name__)

_ERROR_MSG = "Error: All free AI providers failed. Check API keys and try again."


def create_server(intelligence: TaskIntelligence) -> Server:
    server = Server("mcp-taskai")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="taskai_categorize",
                description="Auto-categorize a task into: work, personal, shopping, health, finance, learning, social, errands, other.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task description to categorize",
                        },
                    },
                    "required": ["task"],
                },
            ),
            Tool(
                name="taskai_suggest_deadline",
                description="Suggest a realistic deadline for a task based on its priority and complexity.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task description",
                        },
                        "priority": {
                            "type": "string",
                            "description": "Task priority: low, medium, high, urgent (default: medium)",
                            "enum": ["low", "medium", "high", "urgent"],
                        },
                    },
                    "required": ["task"],
                },
            ),
            Tool(
                name="taskai_detect_duplicates",
                description="Detect duplicate or similar tasks by comparing a new task against existing ones.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "new_task": {
                            "type": "string",
                            "description": "The new task to check for duplicates",
                        },
                        "existing_tasks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of existing task descriptions to compare against",
                        },
                    },
                    "required": ["new_task", "existing_tasks"],
                },
            ),
            Tool(
                name="taskai_summarize",
                description="Summarize a list of tasks (overdue, weekly, or daily summary).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of task descriptions to summarize",
                        },
                        "type": {
                            "type": "string",
                            "description": "Summary type: overdue, weekly, daily (default: overdue)",
                            "enum": ["overdue", "weekly", "daily"],
                        },
                    },
                    "required": ["tasks"],
                },
            ),
            Tool(
                name="taskai_prioritize",
                description="Order tasks by priority with reasoning for each ranking.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of task descriptions to prioritize",
                        },
                    },
                    "required": ["tasks"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "taskai_categorize":
                result = await intelligence.categorize(arguments["task"])
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "taskai_suggest_deadline":
                priority = arguments.get("priority", "medium")
                result = await intelligence.suggest_deadline(arguments["task"], priority)
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "taskai_detect_duplicates":
                result = await intelligence.detect_duplicates(
                    arguments["new_task"], arguments["existing_tasks"]
                )
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "taskai_summarize":
                summary_type = arguments.get("type", "overdue")
                result = await intelligence.summarize(arguments["tasks"], summary_type)
                return [TextContent(type="text", text=result)]

            elif name == "taskai_prioritize":
                result = await intelligence.prioritize(arguments["tasks"])
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except AllProvidersFailedError:
            return [TextContent(type="text", text=_ERROR_MSG)]
        except Exception as exc:
            logger.error("Tool %s failed: %s", name, exc, exc_info=True)
            return [TextContent(type="text", text=f"Error: {exc}")]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
