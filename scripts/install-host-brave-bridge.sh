#!/usr/bin/env bash
# Install the LazyClaw host-Brave-bridge launchd agents (idempotent).
#
# Single source of truth: ~/.lazyclaw/bridge.env
#
#   BRAVE_CDP_PORT       — port Brave listens on for CDP (default 9222, what
#                          the container expects via host.docker.internal)
#   BRAVE_CDP_ADDRESS    — bind address (default 0.0.0.0 so the container can
#                          reach it via the Docker bridge interface)
#   BRAVE_PROFILE_DIR    — Brave profile dir (default: user's regular Brave
#                          profile so existing Upwork/etc. logins are reused)
#   BRAVE_ORIGIN_TOKEN   — shared --remote-allow-origins token. Preserved
#                          across re-installs; mirrored into repo .env as
#                          LAZYCLAW_HOST_CDP_TOKEN so the container picks the
#                          same token at startup.
#   HEAL_ENDPOINT_PORT   — port for the watcher's /heal HTTP endpoint
#                          (default 9224, 127.0.0.1-only).
#
# Both launchd plists are GENERATED from this env file — no hardcoded
# port/path values in the plists, so drift can't recur.
#
# What this does, every run:
#   1. Ensures ~/.lazyclaw/bridge.env exists with defaults (preserving any
#      existing values, including a previously-generated token).
#   2. Writes (overwrites) the two plists from the env values.
#   3. Mirrors BRAVE_ORIGIN_TOKEN into <repo>/.env as LAZYCLAW_HOST_CDP_TOKEN.
#   4. Drops a marker file at <repo>/data/.host_bridge_installed.
#   5. launchctl bootout (best-effort) + bootstrap both plists.
#   6. Polls localhost:${BRAVE_CDP_PORT}/json/version for up to 15s.
#
# Constraints:
#   - We do NOT kill the user's running Brave. If a regular Brave already
#     owns the profile dir, the plist-launched Brave will collide on the
#     SingletonLock and exit. That's the documented gentle-failure case —
#     surfaced both in the installer output AND in the watcher log so the
#     user knows to close Brave once to let the bridge take over.
#
# Uninstall: scripts/uninstall-host-brave-bridge.sh
#
# Usage:
#   bash scripts/install-host-brave-bridge.sh
#   bash scripts/install-host-brave-bridge.sh --browser=chrome

set -euo pipefail

# ── Args ────────────────────────────────────────────────────────────────

BROWSER="brave"
for arg in "$@"; do
    case "$arg" in
        --browser=*) BROWSER="${arg#*=}" ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
    esac
done

# ── Sanity ──────────────────────────────────────────────────────────────

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: this installer is macOS-only (uses launchd)."
    exit 2
fi

# ── Paths ───────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ENV_FILE="$REPO_ROOT/.env"
DATA_DIR="$REPO_ROOT/data"
MARKER_FILE="$DATA_DIR/.host_bridge_installed"

PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_LABEL="sh.lazyclaw.brave-bridge"
PLIST_PATH="$PLIST_DIR/$PLIST_LABEL.plist"
WATCHER_LABEL="sh.lazyclaw.brave-bridge-watcher"
WATCHER_PLIST_PATH="$PLIST_DIR/$WATCHER_LABEL.plist"
WATCHER_SCRIPT_SRC="$REPO_ROOT/scripts/host-bridge-watcher.sh"

# macOS TCC blocks launchd from executing scripts under ~/Desktop. Mirror
# the watcher into a launchd-safe path (the same place cdp-forwarder.py
# and the STT bridge live) and point the plist there.
SUPPORT_DIR="$HOME/Library/Application Support/LazyClaw"
WATCHER_SCRIPT="$SUPPORT_DIR/host-bridge-watcher.sh"

LOG_DIR="$HOME/Library/Logs/LazyClaw"

BRIDGE_CONF_DIR="$HOME/.lazyclaw"
BRIDGE_ENV_FILE="$BRIDGE_CONF_DIR/bridge.env"

mkdir -p "$LOG_DIR" "$DATA_DIR" "$PLIST_DIR" "$BRIDGE_CONF_DIR" "$SUPPORT_DIR"

# ── Detect browser binary ───────────────────────────────────────────────

case "$BROWSER" in
    brave)
        APP_NAME="Brave Browser"
        BIN="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        DEFAULT_PROFILE_DIR="$HOME/Library/Application Support/BraveSoftware/Brave-Browser"
        ;;
    chrome)
        APP_NAME="Google Chrome"
        BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        DEFAULT_PROFILE_DIR="$HOME/Library/Application Support/Google/Chrome"
        ;;
    *)
        echo "Error: --browser must be 'brave' or 'chrome' (got '$BROWSER')."
        exit 2
        ;;
esac

if [[ ! -x "$BIN" ]]; then
    echo "Error: $APP_NAME not found at:"
    echo "       $BIN"
    exit 1
fi

# ── Token discovery: preserve existing token if present anywhere ────────
#
# Priority:
#   1. ~/.lazyclaw/bridge.env  (BRAVE_ORIGIN_TOKEN=...)
#   2. existing plist's --remote-allow-origins=http://lazyclaw-<TOKEN>
#   3. repo .env's LAZYCLAW_HOST_CDP_TOKEN
#   4. generate fresh

read_existing_token() {
    local t=""
    if [[ -f "$BRIDGE_ENV_FILE" ]]; then
        t="$(grep -E '^BRAVE_ORIGIN_TOKEN=' "$BRIDGE_ENV_FILE" 2>/dev/null \
             | tail -n1 | cut -d= -f2- | tr -d '"' || true)"
        [[ -n "$t" ]] && { echo "$t"; return; }
    fi
    if [[ -f "$PLIST_PATH" ]]; then
        t="$(grep -oE 'lazyclaw-[A-Za-z0-9_-]+' "$PLIST_PATH" 2>/dev/null \
             | head -n1 | sed 's/^lazyclaw-//' || true)"
        [[ -n "$t" ]] && { echo "$t"; return; }
    fi
    if [[ -f "$REPO_ENV_FILE" ]]; then
        t="$(grep -E '^LAZYCLAW_HOST_CDP_TOKEN=' "$REPO_ENV_FILE" 2>/dev/null \
             | tail -n1 | cut -d= -f2- || true)"
        [[ -n "$t" ]] && { echo "$t"; return; }
    fi
    echo ""
}

EXISTING_TOKEN="$(read_existing_token)"
if [[ -n "$EXISTING_TOKEN" ]]; then
    TOKEN="$EXISTING_TOKEN"
    echo "→ Reusing existing CDP origin token"
else
    TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))' 2>/dev/null \
            || openssl rand -base64 16 | tr -d '=+/' | cut -c1-22)"
    echo "→ Generated new CDP origin token"
fi

# ── (Re)create ~/.lazyclaw/bridge.env (preserving existing values) ──────

load_or_default() {
    # load_or_default <key> <default>
    local key="$1" default="$2" v=""
    if [[ -f "$BRIDGE_ENV_FILE" ]]; then
        v="$(grep -E "^${key}=" "$BRIDGE_ENV_FILE" 2>/dev/null \
             | tail -n1 | cut -d= -f2- | sed 's/^"//; s/"$//' || true)"
    fi
    if [[ -n "$v" ]]; then
        echo "$v"
    else
        echo "$default"
    fi
}

BRAVE_CDP_PORT="$(load_or_default BRAVE_CDP_PORT 9222)"
BRAVE_CDP_ADDRESS="$(load_or_default BRAVE_CDP_ADDRESS 0.0.0.0)"
BRAVE_PROFILE_DIR="$(load_or_default BRAVE_PROFILE_DIR "$DEFAULT_PROFILE_DIR")"
HEAL_ENDPOINT_PORT="$(load_or_default HEAL_ENDPOINT_PORT 9224)"

cat > "$BRIDGE_ENV_FILE" <<EOF
# LazyClaw host-Brave-bridge configuration (single source of truth).
# Generated by scripts/install-host-brave-bridge.sh — re-running the installer
# preserves any values you've manually edited here.
BRAVE_CDP_PORT=${BRAVE_CDP_PORT}
BRAVE_CDP_ADDRESS=${BRAVE_CDP_ADDRESS}
BRAVE_PROFILE_DIR="${BRAVE_PROFILE_DIR}"
BRAVE_ORIGIN_TOKEN=${TOKEN}
HEAL_ENDPOINT_PORT=${HEAL_ENDPOINT_PORT}
EOF
chmod 600 "$BRIDGE_ENV_FILE"
echo "→ Wrote $BRIDGE_ENV_FILE"

ORIGIN="http://lazyclaw-${TOKEN}"

# ── Generate Brave bridge plist (driven by env vars, no hardcoded ports) ─

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>RunAtLoad</key>
    <true/>

    <!-- Restart on CRASH only. SuccessfulExit=false means a clean Cmd+Q won't
         respawn. The sibling watcher decides when to kickstart. -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>ProgramArguments</key>
    <array>
        <string>${BIN}</string>
        <string>--remote-debugging-port=${BRAVE_CDP_PORT}</string>
        <string>--remote-debugging-address=${BRAVE_CDP_ADDRESS}</string>
        <string>--remote-allow-origins=${ORIGIN}</string>
        <string>--user-data-dir=${BRAVE_PROFILE_DIR}</string>
    </array>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/brave-bridge.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/brave-bridge.err</string>
</dict>
</plist>
EOF
echo "→ Wrote $PLIST_PATH (port=${BRAVE_CDP_PORT}, profile=$(basename "$BRAVE_PROFILE_DIR"))"

# ── Mirror watcher script into launchd-safe support dir ────────────────
#
# macOS TCC denies launchd-spawned processes file access under ~/Desktop
# (and other "Files & Folders" protected locations) unless the user has
# explicitly granted Full Disk Access to launchd. We mirror the script
# into ~/Library/Application Support/LazyClaw/ which IS accessible.
cp "$WATCHER_SCRIPT_SRC" "$WATCHER_SCRIPT"
chmod +x "$WATCHER_SCRIPT"
echo "→ Mirrored watcher to $WATCHER_SCRIPT"

# ── Generate watcher plist ──────────────────────────────────────────────

cat > "$WATCHER_PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${WATCHER_LABEL}</string>

    <key>RunAtLoad</key>
    <true/>

    <!-- Always-on watcher. If it crashes, launchd restarts it within 30s. -->
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${WATCHER_SCRIPT}</string>
    </array>

    <!-- WorkingDirectory intentionally OMITTED — must not point into
         ~/Desktop or any other TCC-protected folder. The watcher uses
         absolute paths everywhere. -->

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/brave-bridge-watcher.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/brave-bridge-watcher.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>BRIDGE_ENV_FILE</key>
        <string>${BRIDGE_ENV_FILE}</string>
        <key>LOG_FILE</key>
        <string>${LOG_DIR}/brave-bridge-watcher.log</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF
echo "→ Wrote $WATCHER_PLIST_PATH"

# ── Update repo .env (mirror token for container) ───────────────────────

if [[ -f "$REPO_ENV_FILE" ]] && grep -qE '^LAZYCLAW_HOST_CDP_TOKEN=' "$REPO_ENV_FILE"; then
    sed -i '' "s|^LAZYCLAW_HOST_CDP_TOKEN=.*|LAZYCLAW_HOST_CDP_TOKEN=${TOKEN}|" "$REPO_ENV_FILE"
    echo "→ Updated LAZYCLAW_HOST_CDP_TOKEN in $REPO_ENV_FILE"
else
    {
        echo ""
        echo "# Host Brave bridge — shared origin token. Mirrors"
        echo "# BRAVE_ORIGIN_TOKEN in ~/.lazyclaw/bridge.env."
        echo "LAZYCLAW_HOST_CDP_TOKEN=${TOKEN}"
    } >> "$REPO_ENV_FILE"
    echo "→ Appended LAZYCLAW_HOST_CDP_TOKEN to $REPO_ENV_FILE"
fi
chmod 600 "$REPO_ENV_FILE" 2>/dev/null || true

# ── Marker file (visible inside the container via ./data:/app/data) ─────

cat > "$MARKER_FILE" <<EOF
# Host Brave bridge installed via scripts/install-host-brave-bridge.sh
# at $(date -u +"%Y-%m-%dT%H:%M:%SZ"). Single source of truth in:
#   $BRIDGE_ENV_FILE
plist=${PLIST_PATH}
browser=${BROWSER}
port=${BRAVE_CDP_PORT}
EOF
echo "→ Wrote marker $MARKER_FILE"

# ── (Re)load both plists ────────────────────────────────────────────────

UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

reload_plist() {
    local label="$1" path="$2"
    launchctl bootout "$DOMAIN/$label" >/dev/null 2>&1 || true
    if launchctl bootstrap "$DOMAIN" "$path" 2>/dev/null; then
        echo "→ bootstrapped $label"
    else
        echo "⚠ bootstrap failed for $label; falling back to legacy load"
        launchctl load "$path" 2>/dev/null || true
    fi
}

reload_plist "$PLIST_LABEL" "$PLIST_PATH"
launchctl kickstart -k "${DOMAIN}/${PLIST_LABEL}" >/dev/null 2>&1 || true

reload_plist "$WATCHER_LABEL" "$WATCHER_PLIST_PATH"
launchctl kickstart -k "${DOMAIN}/${WATCHER_LABEL}" >/dev/null 2>&1 || true

# ── Verify CDP port comes up within 15s ─────────────────────────────────

echo -n "→ Verifying CDP on port ${BRAVE_CDP_PORT} "
PORT_UP=0
for _ in $(seq 1 15); do
    if curl -sf -m 2 "http://localhost:${BRAVE_CDP_PORT}/json/version" >/dev/null 2>&1; then
        PORT_UP=1
        echo " ✓"
        break
    fi
    echo -n "."
    sleep 1
done
[[ $PORT_UP -eq 1 ]] || echo " ✗"

echo ""
if [[ $PORT_UP -eq 1 ]]; then
    echo "✓ Host Brave bridge installed and reachable on localhost:${BRAVE_CDP_PORT}."
    echo ""
    echo "Next:  docker compose restart lazyclaw   # picks up token from .env"
else
    echo "⚠ Bridge installed, but CDP is NOT listening on ${BRAVE_CDP_PORT} yet."
    echo ""
    echo "Most likely cause: your regular Brave is already running and holds the"
    echo "profile lock at:"
    echo "   ${BRAVE_PROFILE_DIR}"
    echo ""
    echo "Quit Brave (Cmd+Q) once; the watcher will kickstart the bridge within"
    echo "30s and it'll take over with --remote-debugging-port=${BRAVE_CDP_PORT}."
    echo ""
    echo "Logs:"
    echo "   ${LOG_DIR}/brave-bridge.err"
    echo "   ${LOG_DIR}/brave-bridge-watcher.log"
    exit 0
fi
