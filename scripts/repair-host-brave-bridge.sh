#!/usr/bin/env bash
# Repair an already-installed host Brave bridge.
#
# What this does:
#   - Patches ~/Library/LaunchAgents/sh.lazyclaw.brave-bridge.plist back to
#     the safe "restart on crash only" config (KeepAlive: SuccessfulExit=false),
#     so launchd never fights a manually-opened Brave for the SingletonLock.
#   - Adds a 10s ThrottleInterval to avoid launchd thrash on hard-fail.
#   - Reloads both plists via launchctl bootout/bootstrap so the change
#     takes effect right away.
#   - Best-effort kickstart so the bridge is reachable now (no-op if a
#     user-opened Brave is already holding the profile).
#
# When to run this:
#   - The agent reports "Brave needs to be open first" while the bridge marker
#     file exists.
#   - `launchctl list | grep brave-bridge` shows PID `-` (loaded but not
#     currently running).
#
# Idempotent — safe to re-run.
#
# Usage:
#   bash scripts/repair-host-brave-bridge.sh

set -euo pipefail

PLIST_LABEL="sh.lazyclaw.brave-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
DOMAIN="gui/$(id -u)"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: macOS-only (uses launchd)."
    exit 2
fi

if [[ ! -f "$PLIST_PATH" ]]; then
    echo "Error: $PLIST_PATH not found."
    echo "Run scripts/install-host-brave-bridge.sh first."
    exit 1
fi

echo "→ Patching $PLIST_PATH"

# Backup once.
cp -n "$PLIST_PATH" "${PLIST_PATH}.bak" 2>/dev/null || true

# Use PlistBuddy — ships with macOS, handles binary + xml plists, no XML hand-rolling.
PB="/usr/libexec/PlistBuddy"

# Set KeepAlive to dict {SuccessfulExit=false}: restart only on crash, not
# on clean exit. This prevents the SingletonLock spawn-storm when a user has
# Brave open manually without --remote-debugging-port.
"$PB" -c "Delete :KeepAlive" "$PLIST_PATH" 2>/dev/null || true
"$PB" -c "Add :KeepAlive dict" "$PLIST_PATH"
"$PB" -c "Add :KeepAlive:SuccessfulExit bool false" "$PLIST_PATH"

# Throttle so a hard-failing Brave doesn't burn CPU.
"$PB" -c "Delete :ThrottleInterval" "$PLIST_PATH" 2>/dev/null || true
"$PB" -c "Add :ThrottleInterval integer 10" "$PLIST_PATH"

# If a manual Brave is already running, it owns the profile SingletonLock.
# Any launchd-spawned Brave with the same profile will die instantly, CDP
# never comes up, and the script "fails" for no obvious reason. Cmd+Q the
# running instance first so launchd can claim the profile cleanly. Brave
# session-restore will bring all tabs back when the bridge relaunches it.
if pgrep -f "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser$" >/dev/null 2>&1; then
    echo "→ Existing Brave detected — quitting it so the bridge can claim the profile"
    echo "  (your tabs will be restored when Brave relaunches with debug enabled)"
    osascript -e 'quit app "Brave Browser"' >/dev/null 2>&1 || true
    # Wait up to 8s for it to exit. SingletonLock release takes a beat.
    for _ in $(seq 1 16); do
        if ! pgrep -f "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser$" >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
    sleep 1   # give the profile lock files a moment to clear
fi

# Clear any stale Singleton* lock files left behind by a force-killed Brave.
PROFILE="$HOME/Library/Application Support/BraveSoftware/Brave-Browser"
for lock in SingletonLock SingletonSocket SingletonCookie; do
    [[ -L "$PROFILE/$lock" || -e "$PROFILE/$lock" ]] && rm -f "$PROFILE/$lock"
done

echo "→ Reloading launchd agent"
launchctl bootout "$DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
if launchctl bootstrap "$DOMAIN" "$PLIST_PATH" 2>/dev/null; then
    echo "→ launchctl bootstrap OK"
else
    echo "⚠ bootstrap failed; falling back to legacy load"
    launchctl load "$PLIST_PATH"
fi

launchctl kickstart -k "${DOMAIN}/${PLIST_LABEL}" >/dev/null 2>&1 || true

# Wait up to 10s for Brave to expose port 9222.
echo -n "→ Waiting for CDP on http://localhost:9222 "
for _ in $(seq 1 10); do
    if curl -sf -m 1 http://localhost:9222/json/version >/dev/null 2>&1; then
        echo "(up)"
        echo ""
        echo "✓ Host Brave bridge repaired. KeepAlive is now always-on."
        echo "  Cmd+Q will auto-relaunch Brave within ~10s."
        echo "  To disable entirely: bash scripts/uninstall-host-brave-bridge.sh"
        exit 0
    fi
    echo -n "."
    sleep 1
done

echo ""
echo "⚠ Brave didn't expose CDP within 10s. Check ~/Library/Logs/LazyClaw/brave-bridge.err"
echo "  Common cause: another Brave window is already open with the profile (SingletonLock)."
echo "  Cmd+Q the existing Brave window and re-run this script."
exit 1
