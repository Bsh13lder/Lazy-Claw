"""mcp-taskai entry point — run as stdio MCP server."""
from __future__ import annotations
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("mcp-taskai")
logger.setLevel(logging.WARNING)


def main() -> None:
    from mcp.server.stdio import stdio_server
    from mcp_taskai.config import load_config
    from mcp_taskai.ai_client import AIClient
    from mcp_taskai.intelligence import TaskIntelligence
    from mcp_taskai.server import create_server

    async def _run() -> None:
        config = load_config()
        ai_client = AIClient(config)
        intelligence = TaskIntelligence(ai_client)
        app = create_server(intelligence)
        logger.info("mcp-taskai starting (providers: %s)", config.provider_names)
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
