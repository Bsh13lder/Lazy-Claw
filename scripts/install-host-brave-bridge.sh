#!/usr/bin/env bash
# Install the LazyClaw host-Brave-bridge launchd agent.
#
# What this does, once:
#   1. Detects your Brave install + profile dir.
#   2. Generates a fresh shared CDP origin token (or reuses an existing one).
#   3. Writes ~/Library/LaunchAgents/sh.lazyclaw.brave-bridge.plist that
#      auto-launches Brave with --remote-debugging-port=9222 every time you
#      log in (and restarts it if Brave crashes — but NOT if you Cmd+Q
#      manually, so you can still close Brave when you want).
#   4. Adds LAZYCLAW_HOST_CDP_TOKEN=<token> to .env in the repo root so the
#      LazyClaw container picks the same token at startup.
#   5. Drops a marker file at data/.host_bridge_installed so use_host_browser
#      can detect "the user has the auto-bridge set up" and message
#      accordingly instead of dumping raw shell commands.
#   6. Loads the plist via launchctl bootstrap (Brave starts immediately).
#
# What this does NOT do:
#   - Open any network ports beyond what Brave itself opens (9222 already).
#   - Auto-start Brave on a fresh boot before login (LaunchAgents fire on
#     user login only — that's intentional, no headless drive-bys).
#   - Install anything system-wide. Plist lives in ~/Library/LaunchAgents/.
#
# Uninstall: scripts/uninstall-host-brave-bridge.sh
#
# Usage:
#   bash scripts/install-host-brave-bridge.sh
#   bash scripts/install-host-brave-bridge.sh --browser=chrome   # use Chrome instead

set -euo pipefail

# ── Args + paths ────────────────────────────────────────────────────────

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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
DATA_DIR="$REPO_ROOT/data"
MARKER_FILE="$DATA_DIR/.host_bridge_installed"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_LABEL="sh.lazyclaw.brave-bridge"
PLIST_PATH="$PLIST_DIR/$PLIST_LABEL.plist"
LOG_DIR="$HOME/Library/Logs/LazyClaw"

# ── Sanity ──────────────────────────────────────────────────────────────

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: this installer is macOS-only (uses launchd). On Linux you can"
    echo "       systemd-equivalent the same idea — file an issue if you need it."
    exit 2
fi

# ── Detect browser binary + profile dir ─────────────────────────────────

case "$BROWSER" in
    brave)
        APP_NAME="Brave Browser"
        BIN="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        PROFILE_DIR="$HOME/Library/Application Support/BraveSoftware/Brave-Browser"
        ;;
    chrome)
        APP_NAME="Google Chrome"
        BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        PROFILE_DIR="$HOME/Library/Application Support/Google/Chrome"
        ;;
    *)
        echo "Error: --browser must be 'brave' or 'chrome' (got '$BROWSER')."
        exit 2
        ;;
esac

if [[ ! -x "$BIN" ]]; then
    echo "Error: $APP_NAME not found at:"
    echo "       $BIN"
    echo "Install it from https://brave.com (or use --browser=chrome)."
    exit 1
fi

mkdir -p "$LOG_DIR" "$DATA_DIR" "$PLIST_DIR"

# ── Token: reuse from .env if present, otherwise generate fresh ─────────

EXISTING_TOKEN=""
if [[ -f "$ENV_FILE" ]]; then
    EXISTING_TOKEN="$(grep -E '^LAZYCLAW_HOST_CDP_TOKEN=' "$ENV_FILE" 2>/dev/null \
                     | tail -n1 | cut -d= -f2- || true)"
fi

if [[ -n "$EXISTING_TOKEN" ]]; then
    TOKEN="$EXISTING_TOKEN"
    echo "→ Reusing existing LAZYCLAW_HOST_CDP_TOKEN from .env"
else
    # 16 random bytes → 22-char URL-safe string. Same shape host_bridge.py uses.
    TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))' 2>/dev/null \
            || openssl rand -base64 16 | tr -d '=+/' | cut -c1-22)"
    echo "→ Generated new CDP origin token"
fi

ORIGIN="http://lazyclaw-${TOKEN}"

# ── Write the plist ─────────────────────────────────────────────────────

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <!-- Run on login, not on system boot — no headless agent before user is present. -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Restart on CRASH only. SuccessfulExit=false means: if Brave exits cleanly
         (you Cmd+Q'd it), don't relaunch. Crash → relaunch. -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>ProgramArguments</key>
    <array>
        <string>${BIN}</string>
        <string>--remote-debugging-port=9222</string>
        <string>--remote-debugging-address=0.0.0.0</string>
        <string>--remote-allow-origins=${ORIGIN}</string>
        <string>--user-data-dir=${PROFILE_DIR}</string>
    </array>

    <!-- Logs for debugging. Truncated by macOS once they grow large. -->
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/brave-bridge.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/brave-bridge.err</string>
</dict>
</plist>
EOF

echo "→ Wrote $PLIST_PATH"

# ── Update .env ─────────────────────────────────────────────────────────

if [[ -f "$ENV_FILE" ]] && grep -qE '^LAZYCLAW_HOST_CDP_TOKEN=' "$ENV_FILE"; then
    # Replace existing line in-place (BSD sed for macOS)
    sed -i '' "s|^LAZYCLAW_HOST_CDP_TOKEN=.*|LAZYCLAW_HOST_CDP_TOKEN=${TOKEN}|" "$ENV_FILE"
    echo "→ Updated LAZYCLAW_HOST_CDP_TOKEN in .env"
else
    {
        echo ""
        echo "# Host Brave bridge — shared origin token. Generated by"
        echo "# scripts/install-host-brave-bridge.sh. The same token lives in the"
        echo "# launchd plist at ~/Library/LaunchAgents/${PLIST_LABEL}.plist."
        echo "LAZYCLAW_HOST_CDP_TOKEN=${TOKEN}"
    } >> "$ENV_FILE"
    echo "→ Appended LAZYCLAW_HOST_CDP_TOKEN to .env"
fi
chmod 600 "$ENV_FILE"

# ── Marker file (visible inside the container via ./data:/app/data) ─────

cat > "$MARKER_FILE" <<EOF
# Host Brave bridge installed via scripts/install-host-brave-bridge.sh
# at $(date -u +"%Y-%m-%dT%H:%M:%SZ"). Read by use_host_browser skill.
plist=${PLIST_PATH}
browser=${BROWSER}
EOF
echo "→ Wrote marker $MARKER_FILE (visible inside the container at /app/data/.host_bridge_installed)"

# ── (Re)load the plist ──────────────────────────────────────────────────

UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

# Bootstrap = load + start. Bootout first so we pick up plist edits cleanly.
launchctl bootout "$DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
if launchctl bootstrap "$DOMAIN" "$PLIST_PATH" 2>/dev/null; then
    echo "→ launchctl bootstrap OK — Brave will start now (and at every login)"
else
    echo "⚠ launchctl bootstrap failed; trying legacy load..."
    launchctl load "$PLIST_PATH"
fi

# Kickstart so Brave starts immediately even if it was already loaded.
launchctl kickstart -k "${DOMAIN}/${PLIST_LABEL}" >/dev/null 2>&1 || true

echo ""
echo "✓ Host Brave bridge installed."
echo ""
echo "Brave is now starting (or restarting) with --remote-debugging-port=9222."
echo "It will auto-relaunch on every login + on crash. To restart manually:"
echo ""
echo "    launchctl kickstart -k ${DOMAIN}/${PLIST_LABEL}"
echo ""
echo "To uninstall: bash scripts/uninstall-host-brave-bridge.sh"
echo ""
echo "Now restart your LazyClaw container so it picks up the new env var:"
echo ""
echo "    docker compose restart lazyclaw"
echo ""
echo "Then in chat: \"use my browser\" — it should connect first try."
