"""
Crawl4AI MCP Server - FastMCP 2.0 Version

Uses FastMCP 2.0.0 which doesn't have banner output issues.
Clean STDIO transport compatible for perfect MCP communication.
"""

import os
import sys
import warnings

# CRITICAL: redirect builtins.print() → stderr BEFORE any other import.
# Every scraper module (and vendored crawl4ai) calls bare print(...) for
# progress lines like "Stage 4/7: Attempting user_behavior". On stdio
# transport those bytes hit fd 1 — the JSONRPC channel — and crash the
# MCP client's parser with `Invalid JSON`. Patching builtins.print catches
# all of them at once without touching every print site.
import builtins as _builtins
_real_print = _builtins.print
def _stderr_print(*args, **kwargs):
    kwargs.setdefault("file", sys.stderr)
    return _real_print(*args, **kwargs)
_builtins.print = _stderr_print

# Set environment variables before any imports
os.environ["FASTMCP_QUIET"] = "true"
os.environ["FASTMCP_NO_BANNER"] = "true"
os.environ["FASTMCP_SILENT"] = "true"
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["TERM"] = "dumb"
os.environ["SHELL"] = "/bin/sh"

warnings.filterwarnings("ignore")
warnings.simplefilter("ignore")

import logging

# Don't suppress all logging — we still need to see real exceptions on stderr.
# `logging.disable(logging.CRITICAL)` (the previous behavior) hid every
# traceback from FastMCP / crawl4ai / Playwright, which made the 2026-04-27
# `Connection closed` crashes invisible. WARNING level keeps the wire quiet
# under normal operation but surfaces actual errors.
logging.basicConfig(
    level=logging.WARNING,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,
)

# Import FastMCP 2.0 - no banner output!
from fastmcp import FastMCP

from .server_tools import register_all_tools

# Create MCP server with clean initialization
mcp = FastMCP("mcp-scraper")

# Tool module loading state (owned by server.py)
_tools_imported = False
_tool_modules = (None, None, None, None, None)


def _load_tool_modules() -> bool:
    """Load tool modules only when needed.

    Returns:
        True if modules were loaded successfully, False otherwise.
    """
    global _tools_imported, _tool_modules
    if _tools_imported:
        return True

    try:
        from .tools import web_crawling as _wc
        from .tools import search as _s
        from .tools import youtube as _yt
        from .tools import file_processing as _fp
        from .tools import utilities as _ut

        _tool_modules = (_wc, _s, _yt, _fp, _ut)
        _tools_imported = True
        return True
    except ImportError:
        try:
            from mcp_scraper.tools import web_crawling as _wc
            from mcp_scraper.tools import search as _s
            from mcp_scraper.tools import youtube as _yt
            from mcp_scraper.tools import file_processing as _fp
            from mcp_scraper.tools import utilities as _ut

            _tool_modules = (_wc, _s, _yt, _fp, _ut)
            _tools_imported = True
            return True
        except ImportError:
            _tools_imported = False
            return False


def is_tools_imported() -> bool:
    """Check if tool modules are imported."""
    return _tools_imported


def get_tool_modules():
    """Get the loaded tool modules.

    Returns:
        Tuple of (web_crawling, search, youtube, file_processing, utilities).
    """
    if not _tools_imported:
        _load_tool_modules()
    return _tool_modules


def _get_modules():
    """Get tool modules, loading them if needed.

    Returns the tuple (web_crawling, search, youtube, file_processing, utilities)
    or None if modules couldn't be loaded.
    """
    if not _tools_imported:
        _load_tool_modules()
    if not _tools_imported:
        return None
    return _tool_modules


# Register all MCP tools
register_all_tools(mcp, _get_modules)


def main():
    """Clean main entry point - FastMCP 2.0 with no banner issues"""
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("Crawl4AI MCP Server - FastMCP 2.0 Version")
        print("Usage: python -m crawl4ai_mcp.server [--transport TRANSPORT]")
        print("Transports: stdio (default), streamable-http, sse")
        return

    # Parse args
    transport = "stdio"
    host = "127.0.0.1"
    port = 8000

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--transport" and i + 1 < len(args):
            transport = args[i + 1]
            i += 2
        elif args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        else:
            i += 1

    # Run server - clean FastMCP 2.0 execution
    try:
        if transport == "stdio":
            mcp.run()
        elif transport in ("streamable-http", "http"):
            mcp.run(transport="streamable-http", host=host, port=port)
        elif transport == "sse":
            mcp.run(transport="sse", host=host, port=port)
        else:
            print(f"Unknown transport: {transport}")
            sys.exit(1)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        if transport != "stdio":
            print(f"Server error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
