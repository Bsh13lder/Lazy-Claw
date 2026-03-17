"""mcp-healthcheck entry point — run as stdio MCP server."""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
for _lib in ("httpx", "httpcore", "urllib3", "hpack"):
    logging.getLogger(_lib).setLevel(logging.WARNING)
logger = logging.getLogger("mcp-healthcheck")


def main() -> None:
    from mcp.server.stdio import stdio_server
    from mcp_healthcheck.config import load_config
    from mcp_healthcheck.monitor import Monitor
    from mcp_healthcheck.server import create_server

    config = load_config()
    monitor = Monitor(config)
    server = create_server(monitor)

    logger.info(
        "mcp-healthcheck starting (endpoints: %s, interval: %ds)",
        monitor.endpoint_names,
        config.ping_interval_seconds,
    )

    async def run() -> None:
        monitor.start_background_loop()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
