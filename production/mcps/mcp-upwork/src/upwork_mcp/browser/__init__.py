"""Browser automation module for Upwork MCP."""

from .client import UpworkBrowser
from .auth import login_interactive

__all__ = ["UpworkBrowser", "login_interactive"]
