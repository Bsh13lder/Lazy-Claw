"""MCP server for project self-healing."""
from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.types import Tool, TextContent

from mcp_lazydoctor.config import LazyDoctorConfig
from mcp_lazydoctor.diagnostics import diagnose_project, run_lint, run_tests
from mcp_lazydoctor.fixer import auto_fix_lint
from mcp_lazydoctor.runner import run_heal_cycle

logger = logging.getLogger(__name__)


def create_server(config: LazyDoctorConfig) -> Server:
    server = Server("mcp-lazydoctor")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="doctor_diagnose",
                description="Run all diagnostics (lint + tests) on the project.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="doctor_lint",
                description="Run linter on the project and report issues.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="doctor_test",
                description="Run tests on the project and report results.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="doctor_fix",
                description="Auto-fix lint issues in the project.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "dry_run": {
                            "type": "boolean",
                            "description": "Preview fixes without applying",
                        },
                    },
                },
            ),
            Tool(
                name="doctor_heal",
                description="Run full diagnose-fix-verify cycle.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "doctor_diagnose":
                results = await diagnose_project(config.project_root)
                output = []
                for r in results:
                    output.append({
                        "tool": r.tool, "success": r.success,
                        "errors": r.errors[:20], "warnings": r.warnings[:10],
                    })
                return [TextContent(type="text", text=json.dumps(output, indent=2))]

            elif name == "doctor_lint":
                result = await run_lint(config.project_root)
                return [TextContent(type="text", text=result.output[:4000])]

            elif name == "doctor_test":
                result = await run_tests(config.project_root)
                return [TextContent(type="text", text=result.output[:4000])]

            elif name == "doctor_fix":
                dry_run = arguments.get("dry_run", config.dry_run)
                result = await auto_fix_lint(config.project_root, dry_run)
                return [TextContent(type="text", text=result[:4000])]

            elif name == "doctor_heal":
                result = await run_heal_cycle(config)
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as exc:
            logger.error("lazydoctor tool %s failed: %s", name, exc)
            return [TextContent(type="text", text=f"Error: {exc}")]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
