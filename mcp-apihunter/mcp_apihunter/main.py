"""mcp-apihunter entry point — run as a stdio MCP server."""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("mcp-apihunter")


def main() -> None:
    from mcp.server.stdio import stdio_server

    from mcp_apihunter.config import load_config
    from mcp_apihunter.server import create_server

    async def _run() -> None:
        config = load_config()
        app = create_server(config)
        logger.info(
            "mcp-apihunter starting (manifest_root=%s, cdp=%s:%s, key=%s)",
            config.manifest_root, config.cdp_host, config.cdp_port,
            "yes" if config.has_manifest_key else "MISSING",
        )
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
