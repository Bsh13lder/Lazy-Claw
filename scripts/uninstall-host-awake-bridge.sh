#!/usr/bin/env bash
# Uninstall the LazyClaw host AWAKE bridge.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER_FILE="$REPO_ROOT/data/.host_awake_bridge_installed"
PLIST_LABEL="sh.lazyclaw.awake-bridge"
PLIST_PATH="/Library/LaunchDaemons/${PLIST_LABEL}.plist"
INSTALL_DIR="/usr/local/lazyclaw/host-awake"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: macOS only."; exit 2
fi

sudo -v

# Stop + unload daemon
if sudo launchctl bootout system "$PLIST_PATH" 2>/dev/null; then
    echo "→ Stopped awake bridge daemon"
elif [[ -f "$PLIST_PATH" ]]; then
    sudo launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

# Remove plist
if [[ -f "$PLIST_PATH" ]]; then
    sudo rm -f "$PLIST_PATH"
    echo "→ Removed $PLIST_PATH"
fi

# Kill any stale caffeinate
PID_FILE="${INSTALL_DIR}/awake.pid"
if sudo test -f "$PID_FILE" 2>/dev/null; then
    PID=$(sudo cat "$PID_FILE" 2>/dev/null || echo "")
    if [[ -n "$PID" ]]; then
        kill "$PID" 2>/dev/null || true
        echo "→ Stopped caffeinate (pid=$PID)"
    fi
    sudo rm -f "$PID_FILE"
fi

# Cancel pmset repeat wake schedule
/usr/bin/pmset repeat cancel 2>/dev/null || true
echo "→ Cleared pmset repeat schedule"

# Remove install dir (contains venv + script)
if [[ -d "$INSTALL_DIR" ]]; then
    sudo rm -rf "$INSTALL_DIR"
    echo "→ Removed $INSTALL_DIR"
fi

# Remove marker
rm -f "$MARKER_FILE"
echo "→ Removed marker file"

echo ""
echo "✓ Host AWAKE bridge uninstalled."
echo "  Restart the container: docker compose restart lazyclaw"
