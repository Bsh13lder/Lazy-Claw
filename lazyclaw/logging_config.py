"""Logging configuration for LazyClaw CLI and server."""

from __future__ import annotations

import logging
import logging.handlers
import warnings
from pathlib import Path


def configure_logging(log_level: str = "WARNING", log_file: str | None = None) -> None:
    """Set up logging: quiet terminal, verbose file.

    - Suppresses noisy libraries (httpx, httpcore, asyncio) on terminal
    - Writes all DEBUG+ output to rotating log file (if path given)
    - Suppresses asyncio RuntimeWarnings
    """
    level = getattr(logging, log_level.upper(), logging.WARNING)

    # Suppress noisy third-party loggers on all handlers
    for name in (
        "httpx", "httpcore", "urllib3", "hpack", "asyncio", "watchfiles",
        "mcp.server.lowlevel.server", "mcp.server", "mcp.client",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Suppress asyncio cleanup noise (Python 3.11+ subprocess watcher warnings)
    warnings.filterwarnings("ignore", category=RuntimeWarning, module="asyncio")
    warnings.filterwarnings("ignore", message=".*that handles pid.*")
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    # Root lazyclaw logger
    root = logging.getLogger("lazyclaw")
    root.setLevel(logging.DEBUG)

    # Clear any existing handlers to avoid duplicates on re-init
    root.handlers.clear()

    # Terminal handler — only show warnings+ by default
    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(stderr_handler)

    # File handler — all debug output for diagnostics
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=3,
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(file_handler)
