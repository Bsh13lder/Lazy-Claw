#!/usr/bin/env bash
# Install the LazyClaw host AWAKE bridge — a root launchd daemon that lets the
# Dockerized agent keep the Mac awake with the lid closed and schedule hardware
# wake-up alarms.
#
# What this does, once:
#   1. Creates a root-owned Python venv at
#      /usr/local/lazyclaw/host-awake/venv with fastapi + uvicorn.
#   2. Copies awake_bridge_server.py to that root-owned tree (never exec
#      from ~/Desktop — TCC-protected + privilege-escalation risk).
#   3. Generates a fresh bearer token (or reuses an existing one).
#   4. Writes /Library/LaunchDaemons/sh.lazyclaw.awake-bridge.plist
#      (root-owned, runs at boot before login, stays alive on crash).
#   5. Adds LAZYCLAW_HOST_AWAKE_TOKEN=<token> to .env so the LazyClaw
#      container picks the same token at startup.
#   6. Drops a marker file at data/.host_awake_bridge_installed so
#      awake_client.py detects "bridge installed" and enables the feature.
#   7. Loads the plist via sudo launchctl bootstrap system (starts immediately).
#
# Why root:
#   `pmset schedule` and `pmset repeat` (needed for scheduled wake-ups) require
#   root. A root LaunchDaemon also starts at boot before login — essential for
#   a headless server box.
#
# Uninstall: scripts/uninstall-host-awake-bridge.sh
# Status:    make awake-bridge-status
# Usage:     bash scripts/install-host-awake-bridge.sh
# Or:        make awake-bridge

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
DATA_DIR="$REPO_ROOT/data"
MARKER_FILE="$DATA_DIR/.host_awake_bridge_installed"
PLIST_LABEL="sh.lazyclaw.awake-bridge"
PLIST_PATH="/Library/LaunchDaemons/${PLIST_LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/LazyClaw"
# Root-owned install dir — NOT under ~/Desktop (TCC-protected for launchd)
INSTALL_DIR="/usr/local/lazyclaw/host-awake"
VENV_DIR="${INSTALL_DIR}/venv"
SERVER_SRC="$REPO_ROOT/scripts/awake_bridge_server.py"
SERVER_DEST="${INSTALL_DIR}/awake_bridge_server.py"
PORT="${LAZYCLAW_HOST_AWAKE_PORT:-18791}"

# ── Sanity ──────────────────────────────────────────────────────────────────

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: this installer is macOS-only (uses launchd + caffeinate + pmset)."
    exit 2
fi

if [[ ! -f "$SERVER_SRC" ]]; then
    echo "Error: server module not found at $SERVER_SRC"
    echo "       Run this from the lazyclaw repo root."
    exit 2
fi

# Confirm sudo intent upfront (pmset schedule/repeat requires root).
echo "→ This installer requires sudo to create a root LaunchDaemon."
echo "  This is necessary because 'pmset schedule' (hardware wake alarms) requires root."
sudo -v

# Find a Python 3.11+ on the host.
HOST_PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        minor=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")
        if [[ "$minor" -ge "11" ]]; then
            HOST_PYTHON="$cmd"
            break
        fi
    fi
done
if [[ -z "$HOST_PYTHON" ]]; then
    echo "Error: Python 3.11+ not found. Install via Homebrew: brew install python@3.13"
    exit 1
fi
echo "→ Using host Python: $($HOST_PYTHON --version) ($HOST_PYTHON)"

mkdir -p "$LOG_DIR" "$DATA_DIR"

# ── Build / refresh the root-owned venv ─────────────────────────────────────

sudo mkdir -p "$INSTALL_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "→ Creating root-owned venv at $VENV_DIR"
    sudo "$HOST_PYTHON" -m venv "$VENV_DIR"
fi

VENV_PY="${VENV_DIR}/bin/python"
echo "→ Installing fastapi + uvicorn into root venv"
sudo "$VENV_PY" -m pip install --quiet --upgrade pip
sudo "$VENV_PY" -m pip install --quiet \
    "fastapi>=0.110.0" \
    "uvicorn[standard]>=0.27.0" \
    "httpx>=0.27.0"
echo "→ Root venv ready"

# ── Copy server script to root-owned path ───────────────────────────────────

echo "→ Copying awake_bridge_server.py to $SERVER_DEST"
sudo cp "$SERVER_SRC" "$SERVER_DEST"
sudo chown root:wheel "$SERVER_DEST"
sudo chmod 644 "$SERVER_DEST"

# ── Token: reuse from .env if present, otherwise generate fresh ─────────────

EXISTING_TOKEN=""
if [[ -f "$ENV_FILE" ]]; then
    EXISTING_TOKEN="$(grep -E '^LAZYCLAW_HOST_AWAKE_TOKEN=' "$ENV_FILE" 2>/dev/null \
                     | tail -n1 | cut -d= -f2- || true)"
fi

if [[ -n "$EXISTING_TOKEN" ]]; then
    TOKEN="$EXISTING_TOKEN"
    echo "→ Reusing existing LAZYCLAW_HOST_AWAKE_TOKEN from .env"
else
    TOKEN="$("$HOST_PYTHON" -c 'import secrets; print(secrets.token_urlsafe(24))')"
    echo "→ Generated new awake bridge bearer token"
fi

# ── Write the plist ──────────────────────────────────────────────────────────

cat > /tmp/sh.lazyclaw.awake-bridge.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <!-- Runs at boot as root (System domain). Starts before login so a
         headless server box can be managed without any user session. -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Restart on any exit (clean or crash). The bridge is the gate for
         all sleep/wake control; it should always be up. -->
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>EnvironmentVariables</key>
    <dict>
        <key>LAZYCLAW_HOST_AWAKE_TOKEN</key>
        <string>${TOKEN}</string>
        <key>LAZYCLAW_HOST_AWAKE_PORT</key>
        <string>${PORT}</string>
        <key>LAZYCLAW_AWAKE_STATE_DIR</key>
        <string>${INSTALL_DIR}</string>
    </dict>

    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PY}</string>
        <string>${SERVER_DEST}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/awake-bridge.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/awake-bridge.err</string>
</dict>
</plist>
EOF

sudo mv /tmp/sh.lazyclaw.awake-bridge.plist "$PLIST_PATH"
sudo chown root:wheel "$PLIST_PATH"
sudo chmod 644 "$PLIST_PATH"
echo "→ Wrote $PLIST_PATH"

# ── Update .env ──────────────────────────────────────────────────────────────

touch "$ENV_FILE"
if grep -qE '^LAZYCLAW_HOST_AWAKE_TOKEN=' "$ENV_FILE"; then
    sed -i '' "s|^LAZYCLAW_HOST_AWAKE_TOKEN=.*|LAZYCLAW_HOST_AWAKE_TOKEN=${TOKEN}|" "$ENV_FILE"
    echo "→ Updated LAZYCLAW_HOST_AWAKE_TOKEN in .env"
else
    {
        echo ""
        echo "# Host AWAKE bridge — shared bearer token. Generated by"
        echo "# scripts/install-host-awake-bridge.sh. Same token lives in"
        echo "# the LaunchDaemon plist at ${PLIST_PATH}."
        echo "LAZYCLAW_HOST_AWAKE_TOKEN=${TOKEN}"
    } >> "$ENV_FILE"
    echo "→ Appended LAZYCLAW_HOST_AWAKE_TOKEN to .env"
fi
chmod 600 "$ENV_FILE"

# ── Marker file (visible inside container via ./data:/app/data) ──────────────

cat > "$MARKER_FILE" <<EOF
# Host AWAKE bridge installed via scripts/install-host-awake-bridge.sh
# at $(date -u +"%Y-%m-%dT%H:%M:%SZ"). Read by lazyclaw/host/awake_client.py.
plist=${PLIST_PATH}
port=${PORT}
EOF
echo "→ Wrote marker $MARKER_FILE"

# ── (Re)load the daemon ───────────────────────────────────────────────────────

sudo launchctl bootout system "$PLIST_PATH" 2>/dev/null || true
if sudo launchctl bootstrap system "$PLIST_PATH" 2>/dev/null; then
    echo "→ launchctl bootstrap OK — awake bridge starting now (and at every boot)"
else
    echo "⚠ launchctl bootstrap failed; trying legacy load..."
    sudo launchctl load "$PLIST_PATH"
fi
sudo launchctl kickstart -k "system/${PLIST_LABEL}" 2>/dev/null || true

# ── Verify ───────────────────────────────────────────────────────────────────

echo "→ Waiting up to 5s for the service to come up..."
for i in 1 2 3 4 5; do
    sleep 1
    if curl -fsS --max-time 1 "http://localhost:${PORT}/health" >/dev/null 2>&1; then
        echo "✓ Awake bridge healthy on http://localhost:${PORT}"
        break
    fi
done

echo ""
echo "✓ Host AWAKE bridge installed."
echo ""
echo "Now restart your LazyClaw container so it picks up the new token:"
echo ""
echo "    docker compose restart lazyclaw"
echo ""
echo "Then say 'stay awake' in Telegram or click the badge in the Web UI."
echo ""
echo "Logs:    tail -f ${LOG_DIR}/awake-bridge.log"
echo "Status:  make awake-bridge-status"
