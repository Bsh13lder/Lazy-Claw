"""mcp-taskai entry point — run as stdio MCP server."""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("mcp-taskai")


def main() -> None:
    from mcp.server.stdio import stdio_server
    from mcp_taskai.config import load_config
    from mcp_taskai.ai_client import AIClient
    from mcp_taskai.intelligence import TaskIntelligence
    from mcp_taskai.server import create_server

    config = load_config()
    client = AIClient(config)
    intelligence = TaskIntelligence(client)
    server = create_server(intelligence)

    logger.info("mcp-taskai starting (providers: %s)", client.provider_names)

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
