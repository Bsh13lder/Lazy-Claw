"""mcp-freeride entry point — run as stdio MCP server."""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
# Suppress noisy HTTP client logs and MCP protocol spam
for _lib in ("httpx", "httpcore", "urllib3", "hpack", "mcp.server.lowlevel.server"):
    logging.getLogger(_lib).setLevel(logging.WARNING)
logger = logging.getLogger("mcp-freeride")


def main() -> None:
    from mcp.server.stdio import stdio_server
    from mcp_freeride.config import load_config
    from mcp_freeride.router import FreeRideRouter
    from mcp_freeride.server import create_server

    config = load_config()
    router = FreeRideRouter(config)
    server = create_server(router)

    logger.info("mcp-freeride starting (providers: %s)", list(router._providers.keys()))

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
