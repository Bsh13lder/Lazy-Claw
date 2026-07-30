#!/usr/bin/env bash
# Install the LazyClaw Funnel watchdog launchd agent (idempotent).
#
# Watches the PUBLIC Tailscale Funnel URL every CHECK_INTERVAL seconds and
# re-registers the funnel (`tailscale serve reset` + `tailscale funnel --bg`)
# after FAIL_THRESHOLD consecutive failures — the fix for the silent
# funnel-wedge-after-LAN-IP-change outage (2026-07-30), which no local
# tailscale signal reports.
#
# Single source of truth: ~/.lazyclaw/funnel-watchdog.env
#
#   FUNNEL_URL          — public funnel base URL (derived from
#                         `tailscale status --json` Self.DNSName on first
#                         install; preserved on re-runs)
#   FUNNEL_BACKEND      — local target the funnel proxies to
#                         (default http://127.0.0.1:3001)
#   CHECK_PATH          — health path appended to both (default /api/health)
#   CHECK_TIMEOUT       — curl timeout seconds (default 10)
#   FAIL_THRESHOLD      — consecutive failures before healing (default 2)
#   HEAL_COOLDOWN_SECS  — min seconds between heals (default 900)
#
# What this does, every run:
#   1. Ensures ~/.lazyclaw/funnel-watchdog.env exists (preserves existing).
#   2. Copies the watchdog script to ~/.lazyclaw/funnel-watchdog.sh —
#      launchd CANNOT exec from ~/Desktop (TCC denies silently, exit 0,
#      '-' PID), so the installed copy lives outside it.
#   3. Generates ~/Library/LaunchAgents/sh.lazyclaw.funnel-watchdog.plist.
#   4. launchctl bootout (best-effort) + bootstrap.
#   5. Runs one check cycle immediately and shows the log tail.
#
# Uninstall: make funnel-watchdog-uninstall
set -euo pipefail

LAZYCLAW_DIR="${HOME}/.lazyclaw"
ENV_FILE="${LAZYCLAW_DIR}/funnel-watchdog.env"
INSTALLED_SCRIPT="${LAZYCLAW_DIR}/funnel-watchdog.sh"
PLIST="${HOME}/Library/LaunchAgents/sh.lazyclaw.funnel-watchdog.plist"
LABEL="sh.lazyclaw.funnel-watchdog"
CHECK_INTERVAL="${CHECK_INTERVAL:-180}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$LAZYCLAW_DIR" "${HOME}/Library/LaunchAgents"

# ── 1. env file (create once, preserve after) ───────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    ts_bin="$(command -v tailscale || echo /opt/homebrew/bin/tailscale)"
    dns_name="$("$ts_bin" status --json 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' \
        2>/dev/null || true)"
    if [ -z "$dns_name" ]; then
        echo "ERROR: could not derive the funnel hostname from tailscale status." >&2
        echo "Create $ENV_FILE manually with FUNNEL_URL=https://<node>.ts.net" >&2
        exit 1
    fi
    cat > "$ENV_FILE" <<EOF
# LazyClaw funnel watchdog — generated $(date '+%Y-%m-%d %H:%M:%S'), edits preserved.
FUNNEL_URL=https://${dns_name}
FUNNEL_BACKEND=http://127.0.0.1:3001
CHECK_PATH=/api/health
CHECK_TIMEOUT=10
FAIL_THRESHOLD=2
HEAL_COOLDOWN_SECS=900
EOF
    echo "wrote $ENV_FILE (FUNNEL_URL=https://${dns_name})"
else
    echo "keeping existing $ENV_FILE"
fi

# ── 2. install the watchdog outside ~/Desktop (TCC) ─────────────────────
cp "$REPO_DIR/scripts/funnel-watchdog.sh" "$INSTALLED_SCRIPT"
chmod +x "$INSTALLED_SCRIPT"

# ── 3. generate the plist ───────────────────────────────────────────────
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${INSTALLED_SCRIPT}</string>
    </array>
    <key>StartInterval</key><integer>${CHECK_INTERVAL}</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>${LAZYCLAW_DIR}/funnel-watchdog.launchd.log</string>
    <key>StandardErrorPath</key><string>${LAZYCLAW_DIR}/funnel-watchdog.launchd.log</string>
</dict>
</plist>
EOF

# ── 4. (re)load ─────────────────────────────────────────────────────────
uid="$(id -u)"
launchctl bootout "gui/${uid}" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/${uid}" "$PLIST"
echo "loaded ${LABEL} (every ${CHECK_INTERVAL}s)"

# ── 5. immediate cycle + proof ──────────────────────────────────────────
bash "$INSTALLED_SCRIPT" || true
echo "── log tail ──"
tail -n 5 "${LAZYCLAW_DIR}/funnel-watchdog.log" 2>/dev/null || echo "(log empty — healthy steady-state is silent)"
