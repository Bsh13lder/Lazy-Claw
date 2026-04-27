"""Entry point for `python -m mcp_scraper`.

Matches the LazyClaw BUNDLED_MCPS contract — the manager spawns each
bundled MCP via `sys.executable -m <module>` and talks to it over stdio.
"""

from mcp_scraper.server import main

if __name__ == "__main__":
    main()
