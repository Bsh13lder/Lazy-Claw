"""Configuration management for Upwork MCP."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base directories
DATA_DIR = Path.home() / ".upwork-mcp"
PROFILE_DIR = DATA_DIR / "profile"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"


def ensure_dirs():
    """Create necessary directories if they don't exist."""
    for d in [DATA_DIR, PROFILE_DIR, CACHE_DIR, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)


# Browser settings
BROWSER_HEADLESS = os.getenv("UPWORK_MCP_HEADLESS", "true").lower() == "true"
BROWSER_TIMEOUT = int(os.getenv("UPWORK_MCP_TIMEOUT", "30000"))

# Upwork URLs
UPWORK_BASE_URL = "https://www.upwork.com"
UPWORK_LOGIN_URL = f"{UPWORK_BASE_URL}/ab/account-security/login"
UPWORK_JOBS_URL = f"{UPWORK_BASE_URL}/nx/find-work"
UPWORK_PROPOSALS_URL = f"{UPWORK_BASE_URL}/nx/proposals"
UPWORK_MESSAGES_URL = f"{UPWORK_BASE_URL}/nx/messages"
UPWORK_CONTRACTS_URL = f"{UPWORK_BASE_URL}/nx/wm/contracts"
UPWORK_PROFILE_URL = f"{UPWORK_BASE_URL}/freelancers/settings/profile"
