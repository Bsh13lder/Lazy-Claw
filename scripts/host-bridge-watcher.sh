#!/usr/bin/env bash
# Host-side watcher for the LazyClaw Brave bridge.
#
# Decides — every 15s — whether the bridge needs a kick. Conservative by
# design: NEVER spawns a duplicate Brave when the user already has one open
# manually. The Brave launchd plist is left at "restart on crash only", so
# this watcher is the sole place that decides "kickstart the bridge plist".
#
# Logic:
#   1. If the container raised a repair flag → kickstart, clear flag, sleep.
#   2. If CDP probe is healthy → noop.
#   3. If CDP is down AND no Brave main process is running → kickstart (the
#      bridge died, bring it back).
#   4. If CDP is down BUT a Brave main process IS running → user opened
#      Brave manually without --remote-debugging-port. We log it and stand
#      down. Spawning another Brave just hits SingletonLock and burns CPU.
#
# Install/Uninstall via the launchd plist sibling installed by
# scripts/install-host-brave-bridge.sh. No network ports of its own.
#
# Usage:
#   bash scripts/host-bridge-watcher.sh [--once]   # --once for testing

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd \
    || echo "/Users/$(whoami)/Desktop/Code_Projects/lazyclaw")"
DATA_DIR="$REPO_ROOT/data"
FLAG_FILE="$DATA_DIR/.host_bridge_repair_needed"
PLIST_LABEL="sh.lazyclaw.brave-bridge"
DOMAIN="gui/$(id -u)"
KICKSTART_TARGET="${DOMAIN}/${PLIST_LABEL}"

ONCE=0
[[ "${1:-}" == "--once" ]] && ONCE=1

stamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log()   { echo "[$(stamp)] $*"; }

probe_cdp() {
    curl -sf -m 2 http://localhost:9222/json/version >/dev/null 2>&1
}

brave_running() {
    # Match ONLY the main Brave process, not the helpers.
    pgrep -f '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser$' >/dev/null 2>&1
}

kickstart_brave() {
    log "kickstarting $KICKSTART_TARGET"
    launchctl kickstart -k "$KICKSTART_TARGET" >/dev/null 2>&1 \
        || log "  ⚠ kickstart failed — plist may not be loaded"
}

check_once() {
    # 1. Container-requested repair takes priority.
    if [[ -f "$FLAG_FILE" ]]; then
        log "container raised repair flag"
        if brave_running && ! probe_cdp; then
            log "  user has Brave open without debug port — leaving it alone"
            rm -f "$FLAG_FILE" || true
            return
        fi
        kickstart_brave
        rm -f "$FLAG_FILE" || true
        sleep 8
        return
    fi

    # 2. Healthy → noop.
    if probe_cdp; then
        return
    fi

    # 3. CDP down. Only kick when no manually-opened Brave is in the way.
    if brave_running; then
        # User has Brave open without debug args. Don't fight them.
        return
    fi

    sleep 3   # 2-strike confirmation
    if probe_cdp; then return; fi
    if brave_running; then return; fi

    log "CDP down + no user Brave — recovering bridge"
    kickstart_brave
    sleep 8
}

if [[ "$ONCE" -eq 1 ]]; then
    check_once
    exit 0
fi

log "host-bridge-watcher started (data=$DATA_DIR)"
while true; do
    check_once
    sleep 15
done
