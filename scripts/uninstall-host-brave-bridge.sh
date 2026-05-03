#!/usr/bin/env bash
# Uninstall the LazyClaw host-Brave-bridge launchd agent.
#
# Removes:
#   - The launchd plist + its loaded state
#   - The data/.host_bridge_installed marker
#   - The LAZYCLAW_HOST_CDP_TOKEN line in .env
#
# Does NOT touch your Brave profile or your tabs. Brave will keep running
# until you Cmd+Q it (then it won't auto-restart anymore).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
MARKER_FILE="$REPO_ROOT/data/.host_bridge_installed"
PLIST_LABEL="sh.lazyclaw.brave-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
DOMAIN="gui/$(id -u)"

echo "→ Stopping + unloading the launchd agent"
launchctl bootout "$DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 \
    || launchctl unload "$PLIST_PATH" >/dev/null 2>&1 \
    || true

if [[ -f "$PLIST_PATH" ]]; then
    rm "$PLIST_PATH"
    echo "→ Removed $PLIST_PATH"
fi

if [[ -f "$MARKER_FILE" ]]; then
    rm "$MARKER_FILE"
    echo "→ Removed $MARKER_FILE"
fi

if [[ -f "$ENV_FILE" ]] && grep -qE '^LAZYCLAW_HOST_CDP_TOKEN=' "$ENV_FILE"; then
    sed -i '' '/^LAZYCLAW_HOST_CDP_TOKEN=/d' "$ENV_FILE"
    # Drop the explanatory comment line if it's still there
    sed -i '' '/^# Host Brave bridge — shared origin token/,/^# launchd plist at /d' "$ENV_FILE" 2>/dev/null || true
    echo "→ Removed LAZYCLAW_HOST_CDP_TOKEN from .env"
fi

echo ""
echo "✓ Uninstalled. Restart LazyClaw container to drop the env var:"
echo ""
echo "    docker compose restart lazyclaw"
