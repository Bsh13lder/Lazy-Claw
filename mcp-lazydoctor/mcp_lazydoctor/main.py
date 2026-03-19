"""mcp-lazydoctor entry point — run as stdio MCP server."""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
for _lib in ("httpx", "httpcore", "urllib3", "mcp.server.lowlevel.server"):
    logging.getLogger(_lib).setLevel(logging.WARNING)
logger = logging.getLogger("mcp-lazydoctor")


def main() -> None:
    from mcp.server.stdio import stdio_server
    from mcp_lazydoctor.config import load_config
    from mcp_lazydoctor.server import create_server

    config = load_config()
    server = create_server(config)

    logger.info(
        "mcp-lazydoctor starting (project: %s, auto_fix: %s, dry_run: %s)",
        config.project_root,
        config.auto_fix_enabled,
        config.dry_run,
    )

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
