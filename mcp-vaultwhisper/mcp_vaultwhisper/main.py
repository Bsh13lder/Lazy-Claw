"""mcp-vaultwhisper entry point — run as stdio MCP server."""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
for _lib in ("httpx", "httpcore", "urllib3", "hpack", "mcp.server.lowlevel.server"):
    logging.getLogger(_lib).setLevel(logging.WARNING)
logger = logging.getLogger("mcp-vaultwhisper")


def main() -> None:
    from mcp.server.stdio import stdio_server
    from mcp_vaultwhisper.config import load_config, get_configured_providers
    from mcp_vaultwhisper.patterns import get_active_patterns
    from mcp_vaultwhisper.server import create_server

    config = load_config()
    patterns = get_active_patterns(config.mode, config.custom_patterns_json)
    server = create_server(config, patterns)

    providers = get_configured_providers(config)
    logger.info(
        "mcp-vaultwhisper starting (mode: %s, patterns: %d, providers: %s)",
        config.mode,
        len(patterns),
        providers,
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
