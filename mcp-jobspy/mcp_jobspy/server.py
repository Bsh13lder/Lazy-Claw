"""MCP server wrapping python-jobspy for multi-platform job search."""

from __future__ import annotations

import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

app = Server("mcp-jobspy")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_jobs",
            description=(
                "Search for jobs across Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google. "
                "Returns job listings with title, company, location, salary, URL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "Job search keywords (e.g. 'python developer')",
                    },
                    "location": {
                        "type": "string",
                        "description": "Location filter (e.g. 'Remote', 'New York')",
                        "default": "Remote",
                    },
                    "site_name": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Platforms: indeed, linkedin, glassdoor, zip_recruiter, google",
                        "default": ["indeed", "glassdoor"],
                    },
                    "results_wanted": {
                        "type": "integer",
                        "description": "Max results per platform (default 10)",
                        "default": 10,
                    },
                    "hours_old": {
                        "type": "integer",
                        "description": "Only jobs posted within this many hours (default 72)",
                        "default": 72,
                    },
                    "is_remote": {
                        "type": "boolean",
                        "description": "Filter for remote jobs only",
                        "default": False,
                    },
                },
                "required": ["search_term"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "search_jobs":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    try:
        return [TextContent(type="text", text=await _search(arguments))]
    except Exception as exc:
        logger.error("Job search failed: %s", exc, exc_info=True)
        return [TextContent(type="text", text=f"Job search failed: {exc}")]


async def _search(args: dict) -> str:
    """Run python-jobspy scrape in a thread (it's synchronous + network I/O)."""
    import asyncio

    from jobspy import scrape_jobs

    from .normalize import normalize_row

    search_term = args.get("search_term", "")
    location = args.get("location", "Remote")
    site_name = args.get("site_name", ["indeed", "glassdoor"])
    results_wanted = min(int(args.get("results_wanted", 10)), 50)
    hours_old = int(args.get("hours_old", 72))
    is_remote = bool(args.get("is_remote", False))

    loop = asyncio.get_running_loop()
    df = await loop.run_in_executor(
        None,
        lambda: scrape_jobs(
            site_name=site_name,
            search_term=search_term,
            location=location,
            results_wanted=results_wanted,
            hours_old=hours_old,
            is_remote=is_remote,
        ),
    )

    if df is None or df.empty:
        return json.dumps({"jobs": [], "message": f"No jobs found for '{search_term}'"})

    jobs = [normalize_row(row) for _, row in df.iterrows()]
    return json.dumps({"jobs": jobs, "total": len(jobs)})


def main() -> None:
    """Run the MCP server on stdio."""
    import asyncio

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    asyncio.run(_run())
