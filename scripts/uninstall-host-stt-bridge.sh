#!/usr/bin/env bash
# Uninstall the LazyClaw host-STT-bridge launchd agent.
#
# Removes the launchd plist, the marker file, and (with --purge) the venv
# at ~/Library/Application Support/LazyClaw/host-stt/. Leaves the
# LAZYCLAW_HOST_STT_TOKEN line in .env alone so reinstall keeps the same
# token (handy for rolling restarts) — pass --purge to nuke that too.
#
# Usage:
#   bash scripts/uninstall-host-stt-bridge.sh
#   bash scripts/uninstall-host-stt-bridge.sh --purge   # also remove venv + .env line

set -euo pipefail

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
MARKER_FILE="$REPO_ROOT/data/.host_stt_bridge_installed"
PLIST_LABEL="sh.lazyclaw.stt-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
VENV_DIR="$HOME/Library/Application Support/LazyClaw/host-stt/venv"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

# Stop + unload
launchctl bootout "$DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true

if [[ -f "$PLIST_PATH" ]]; then
    rm -f "$PLIST_PATH"
    echo "→ Removed $PLIST_PATH"
fi

if [[ -f "$MARKER_FILE" ]]; then
    rm -f "$MARKER_FILE"
    echo "→ Removed $MARKER_FILE"
fi

if [[ "$PURGE" -eq 1 ]]; then
    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        echo "→ Removed venv $VENV_DIR"
    fi
    if [[ -f "$ENV_FILE" ]] && grep -qE '^LAZYCLAW_HOST_STT_TOKEN=' "$ENV_FILE"; then
        sed -i '' '/^LAZYCLAW_HOST_STT_TOKEN=/d' "$ENV_FILE"
        # Also drop the explanatory comment block we wrote above the line
        sed -i '' '/^# Host STT bridge — shared bearer token/,/^# the launchd plist/d' "$ENV_FILE"
        echo "→ Removed LAZYCLAW_HOST_STT_TOKEN from .env"
    fi
fi

echo ""
echo "✓ Host STT bridge uninstalled."
echo "  Container voice notes will now use CPU whisper inside Docker."
echo "  Reinstall any time: make host-stt"
