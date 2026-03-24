"""mcp-lazydoctor entry point — run as stdio MCP server."""
from __future__ import annotations
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("mcp-lazydoctor")
logger.setLevel(logging.WARNING)


def main() -> None:
    from mcp.server.stdio import stdio_server
    from mcp_lazydoctor.config import load_config
    from mcp_lazydoctor.server import create_server

    async def _run() -> None:
        config = load_config()
        app = create_server(config)
        logger.info("mcp-lazydoctor starting (project: %s, auto_fix: %s, dry_run: %s)",
                     config.project_root, config.auto_fix_enabled, config.dry_run)
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
