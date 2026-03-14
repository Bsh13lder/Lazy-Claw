#!/usr/bin/env python3
"""
LazyClaw Computer Connector

Standalone program that runs on your desktop computer.
Connects to your LazyClaw server via WebSocket so the AI agent
can execute commands, read/write files, take screenshots, etc.

Usage:
    python main.py                  # Normal start (runs setup wizard on first launch)
    python main.py --no-approval    # Don't ask before executing commands
    python main.py --reset          # Re-run setup wizard
"""
import argparse
import asyncio
import getpass
import json
import logging
import os
import signal
import sys

import httpx

from connector import Connector

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".lazyclaw")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def load_config() -> dict | None:
    """Load saved config from ~/.lazyclaw/config.json."""
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_config(config: dict):
    """Save config to ~/.lazyclaw/config.json."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)


def setup_wizard() -> dict:
    """Interactive setup: get server URL, credentials, obtain token."""
    print()
    print("=" * 50)
    print("  LazyClaw Computer Connector — Setup")
    print("=" * 50)
    print()

    server_url = input("Server URL (e.g. http://localhost:18789): ").strip()
    if not server_url:
        print("Error: Server URL is required.")
        sys.exit(1)
    server_url = server_url.rstrip('/')

    username = input("Username: ").strip()
    if not username:
        print("Error: Username is required.")
        sys.exit(1)
    password = getpass.getpass("Password: ")
    if not password:
        print("Error: Password is required.")
        sys.exit(1)

    print(f"\nConnecting to {server_url}...")
    try:
        resp = httpx.post(
            f"{server_url}/api/connector/token",
            json={"username": username, "password": password},
            timeout=15,
        )
        if resp.status_code == 401:
            print("Error: Invalid username or password.")
            sys.exit(1)
        if resp.status_code != 200:
            print(f"Error: Server returned {resp.status_code}: {resp.text}")
            sys.exit(1)

        data = resp.json()
        token = data.get("token")
        if not token:
            print("Error: No token received from server.")
            sys.exit(1)

    except httpx.ConnectError:
        print(f"Error: Cannot connect to {server_url}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    config = {
        "server_url": server_url,
        "connector_token": token,
        "require_approval": True,
    }
    save_config(config)
    print(f"\nSetup complete! Config saved to {CONFIG_FILE}")
    print("Your computer will appear as connected in LazyClaw.\n")
    return config


def main():
    parser = argparse.ArgumentParser(description="LazyClaw Computer Connector")
    parser.add_argument('--no-approval', action='store_true',
                        help="Don't ask before executing commands")
    parser.add_argument('--reset', action='store_true',
                        help="Re-run setup wizard")
    args = parser.parse_args()

    config = None if args.reset else load_config()
    if config is None:
        config = setup_wizard()

    if args.no_approval:
        config['require_approval'] = False

    print()
    print("\033[1;36m" + "=" * 50 + "\033[0m")
    print("\033[1;36m  LazyClaw Computer Connector\033[0m")
    print("\033[1;36m" + "=" * 50 + "\033[0m")
    print(f"  Server:   {config['server_url']}")
    approval_msg = (
        'ON — will ask before each command'
        if config.get('require_approval', True)
        else 'OFF — commands execute automatically'
    )
    print(f"  Approval: {approval_msg}")
    print(f"  Press Ctrl+C to disconnect")
    print()

    connector = Connector(config)

    def _signal_handler(sig, frame):
        print("\n\nDisconnecting...")
        connector.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        asyncio.run(connector.run())
    except KeyboardInterrupt:
        pass

    print("Disconnected.")


if __name__ == "__main__":
    main()
