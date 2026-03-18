"""mcp-apihunter entry point — run as stdio MCP server."""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
for _lib in ("httpx", "httpcore", "urllib3", "hpack", "mcp.server.lowlevel.server"):
    logging.getLogger(_lib).setLevel(logging.WARNING)
logger = logging.getLogger("mcp-apihunter")


def main() -> None:
    from mcp.server.stdio import stdio_server
    from mcp_apihunter.config import load_config
    from mcp_apihunter.registry import Registry
    from mcp_apihunter.server import create_server

    config = load_config()
    registry = Registry(config.db_path)
    server = create_server(registry, config)

    logger.info("mcp-apihunter starting (db: %s, auto_validate: %s)", config.db_path, config.auto_validate)

    async def run():
        await registry.init_db()
        await registry.seed_known_providers()

        if config.scan_on_startup:
            from mcp_apihunter.scanner import run_full_scan

            report = await run_full_scan(registry, config)
            logger.info(
                "Startup scan: discovered=%d added=%d updated=%d errors=%d",
                report.discovered, report.added, report.updated, len(report.errors),
            )

        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
