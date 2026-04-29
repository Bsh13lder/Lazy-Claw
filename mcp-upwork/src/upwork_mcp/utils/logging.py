"""Logging setup for Upwork MCP."""

import logging
import sys
from pathlib import Path
from .config import LOG_DIR, ensure_dirs


def setup_logging(
    level: int = logging.INFO,
    log_to_file: bool = True,
    log_file: str = "upwork-mcp.log",
) -> logging.Logger:
    """Set up logging for the application.

    Args:
        level: Logging level
        log_to_file: Whether to also log to file
        log_file: Name of the log file

    Returns:
        Configured logger
    """
    logger = logging.getLogger("upwork-mcp")
    logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_format = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler
    if log_to_file:
        ensure_dirs()
        file_handler = logging.FileHandler(LOG_DIR / log_file)
        file_handler.setLevel(level)
        file_format = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    return logger


# Default logger
logger = setup_logging()
