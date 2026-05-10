#!/usr/bin/env bash
# Install the LazyClaw host-STT-bridge launchd agent.
#
# What this does, once:
#   1. Creates a small Python venv at
#      ~/Library/Application Support/LazyClaw/host-stt/venv
#      with pywhispercpp + imageio-ffmpeg + fastapi + uvicorn.
#   2. Generates a fresh shared bearer token (or reuses an existing one).
#   3. Writes ~/Library/LaunchAgents/sh.lazyclaw.stt-bridge.plist that
#      auto-runs the FastAPI service on localhost:18790 every time you log
#      in (and restarts it if it crashes).
#   4. Adds LAZYCLAW_HOST_STT_TOKEN=<token> to .env so the LazyClaw
#      container picks the same token at startup.
#   5. Drops a marker file at data/.host_stt_bridge_installed so stt.py
#      detects "the user has the host bridge set up" and POSTs audio to
#      it instead of running CPU whisper inside the container.
#   6. Loads the plist via launchctl bootstrap (service starts immediately).
#
# Why this exists:
#   Docker on macOS runs a Linux VM. Linux containers cannot access Metal
#   or Core ML — those are macOS-host-only frameworks. Running whisper.cpp
#   on the host gives ~3-5x speedup on Apple Silicon. See
#   lazyclaw/audio/stt_host_server.py for the security model.
#
# Uninstall: scripts/uninstall-host-stt-bridge.sh
#
# Usage: bash scripts/install-host-stt-bridge.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
DATA_DIR="$REPO_ROOT/data"
MARKER_FILE="$DATA_DIR/.host_stt_bridge_installed"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_LABEL="sh.lazyclaw.stt-bridge"
PLIST_PATH="$PLIST_DIR/$PLIST_LABEL.plist"
LOG_DIR="$HOME/Library/Logs/LazyClaw"
VENV_DIR="$HOME/Library/Application Support/LazyClaw/host-stt/venv"
SERVER_MODULE="$REPO_ROOT/lazyclaw/audio/stt_host_server.py"
PORT="${LAZYCLAW_HOST_STT_PORT:-18790}"

# ── Sanity ──────────────────────────────────────────────────────────────

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: this installer is macOS-only (uses launchd + targets Metal)."
    echo "       On a Linux host, the container's CPU whisper.cpp is already"
    echo "       running on the same kernel — no bridge needed."
    exit 2
fi

if [[ ! -f "$SERVER_MODULE" ]]; then
    echo "Error: server module not found at $SERVER_MODULE"
    echo "       Run this from the lazyclaw repo root."
    exit 2
fi

# Find a Python 3.11+ on the host (independent of the container).
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
    echo "Error: Python 3.11+ not found on host. Install via Homebrew:"
    echo "       brew install python@3.13"
    exit 1
fi
echo "→ Using host Python: $($HOST_PYTHON --version) ($HOST_PYTHON)"

mkdir -p "$LOG_DIR" "$DATA_DIR" "$PLIST_DIR" "$(dirname "$VENV_DIR")"

# ── Build / refresh the host venv ───────────────────────────────────────

if [[ ! -d "$VENV_DIR" ]]; then
    echo "→ Creating venv at $VENV_DIR"
    "$HOST_PYTHON" -m venv "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
echo "→ Installing pywhispercpp + imageio-ffmpeg + fastapi + uvicorn into venv"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet \
    "pywhispercpp>=1.2.0" \
    "imageio-ffmpeg>=0.4.9" \
    "fastapi>=0.110.0" \
    "uvicorn[standard]>=0.27.0" \
    "python-multipart>=0.0.7"
echo "→ Host venv ready"

# ── Token: reuse from .env if present, otherwise generate fresh ─────────

EXISTING_TOKEN=""
if [[ -f "$ENV_FILE" ]]; then
    EXISTING_TOKEN="$(grep -E '^LAZYCLAW_HOST_STT_TOKEN=' "$ENV_FILE" 2>/dev/null \
                     | tail -n1 | cut -d= -f2- || true)"
fi

if [[ -n "$EXISTING_TOKEN" ]]; then
    TOKEN="$EXISTING_TOKEN"
    echo "→ Reusing existing LAZYCLAW_HOST_STT_TOKEN from .env"
else
    TOKEN="$("$HOST_PYTHON" -c 'import secrets; print(secrets.token_urlsafe(24))')"
    echo "→ Generated new STT bearer token"
fi

# ── Write the plist ─────────────────────────────────────────────────────

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>RunAtLoad</key>
    <true/>

    <!-- Always-on. If the FastAPI process exits (clean or crash), launchd
         restarts it within ThrottleInterval seconds. The bridge is the
         performance fast-path — if it's down, the container falls back to
         CPU whisper, so a brief restart gap is fine. -->
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>EnvironmentVariables</key>
    <dict>
        <key>LAZYCLAW_HOST_STT_TOKEN</key>
        <string>${TOKEN}</string>
        <key>LAZYCLAW_HOST_STT_PORT</key>
        <string>${PORT}</string>
    </dict>

    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PY}</string>
        <string>${SERVER_MODULE}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_ROOT}</string>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/stt-bridge.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/stt-bridge.err</string>
</dict>
</plist>
EOF
echo "→ Wrote $PLIST_PATH"

# ── Update .env ─────────────────────────────────────────────────────────

touch "$ENV_FILE"
if grep -qE '^LAZYCLAW_HOST_STT_TOKEN=' "$ENV_FILE"; then
    sed -i '' "s|^LAZYCLAW_HOST_STT_TOKEN=.*|LAZYCLAW_HOST_STT_TOKEN=${TOKEN}|" "$ENV_FILE"
    echo "→ Updated LAZYCLAW_HOST_STT_TOKEN in .env"
else
    {
        echo ""
        echo "# Host STT bridge — shared bearer token. Generated by"
        echo "# scripts/install-host-stt-bridge.sh. The same token lives in"
        echo "# the launchd plist at ${PLIST_PATH}."
        echo "LAZYCLAW_HOST_STT_TOKEN=${TOKEN}"
    } >> "$ENV_FILE"
    echo "→ Appended LAZYCLAW_HOST_STT_TOKEN to .env"
fi
chmod 600 "$ENV_FILE"

# ── Marker file (visible inside the container via ./data:/app/data) ─────

cat > "$MARKER_FILE" <<EOF
# Host STT bridge installed via scripts/install-host-stt-bridge.sh
# at $(date -u +"%Y-%m-%dT%H:%M:%SZ"). Read by lazyclaw/audio/stt.py.
plist=${PLIST_PATH}
port=${PORT}
EOF
echo "→ Wrote marker $MARKER_FILE (visible inside the container as /app/data/.host_stt_bridge_installed)"

# ── (Re)load the plist ──────────────────────────────────────────────────

UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

launchctl bootout "$DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
if launchctl bootstrap "$DOMAIN" "$PLIST_PATH" 2>/dev/null; then
    echo "→ launchctl bootstrap OK — STT bridge starting now (and at every login)"
else
    echo "⚠ launchctl bootstrap failed; trying legacy load..."
    launchctl load "$PLIST_PATH"
fi
launchctl kickstart -k "${DOMAIN}/${PLIST_LABEL}" >/dev/null 2>&1 || true

# ── Verify ──────────────────────────────────────────────────────────────

echo "→ Waiting up to 5s for the service to come up..."
for i in 1 2 3 4 5; do
    sleep 1
    if curl -fsS --max-time 1 "http://localhost:${PORT}/health" >/dev/null 2>&1; then
        echo "✓ Host STT bridge healthy on http://localhost:${PORT}"
        break
    fi
done

echo ""
echo "✓ Host STT bridge installed."
echo ""
echo "Now restart your LazyClaw container so it picks up the new token:"
echo ""
echo "    docker compose restart lazyclaw"
echo ""
echo "First voice note will trigger a one-time model download (~465 MB for"
echo "'small') ON THE HOST this time, with Metal acceleration. Subsequent"
echo "transcriptions take ~1-2 s for short clips."
echo ""
echo "Logs:    tail -f ${LOG_DIR}/stt-bridge.log"
echo "Status:  make host-stt-status"
echo "Restart: make host-stt-restart"
