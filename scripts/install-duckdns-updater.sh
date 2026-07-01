#!/usr/bin/env bash
# Installs a launchd agent that refreshes the DuckDNS record every 5 minutes,
# so the subdomain follows the home IP if the ISP ever changes it. Mirrors the
# existing com.lazyclaw.ngrok agent pattern.
#
# One-time setup before running this:
#   mkdir -p ~/.lazyclaw
#   printf 'DUCKDNS_DOMAIN=lazyclaw\nDUCKDNS_TOKEN=YOUR_TOKEN\n' > ~/.lazyclaw/duckdns.env
#   chmod 600 ~/.lazyclaw/duckdns.env
# Then: ./scripts/install-duckdns-updater.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/scripts/duckdns-update.sh"

# macOS TCC denies launchd execute on ~/Desktop (job runs but exits "Operation
# not permitted"), so mirror the script to a TCC-safe location and run THAT copy
# from launchd. Same defense as the host-Brave-bridge installer.
APPSUP="$HOME/Library/Application Support/LazyClaw"
mkdir -p "$APPSUP"
SCRIPT="$APPSUP/duckdns-update.sh"
cp "$SRC" "$SCRIPT"
chmod +x "$SCRIPT"

CONF="$HOME/.lazyclaw/duckdns.env"
if [ ! -f "$CONF" ]; then
	echo "ERROR: $CONF not found. Create it first (see header of this script)." >&2
	exit 1
fi

PLIST="$HOME/Library/LaunchAgents/com.lazyclaw.duckdns.plist"
LOG="$HOME/Library/Logs/lazyclaw-duckdns.log"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>com.lazyclaw.duckdns</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>$SCRIPT</string>
	</array>
	<key>RunAtLoad</key><true/>
	<key>StartInterval</key><integer>300</integer>
	<key>StandardOutPath</key><string>$LOG</string>
	<key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed DuckDNS updater (every 300s). Log: $LOG"
echo "First run:"
DUCKDNS_CONF="$CONF" /bin/bash "$SCRIPT" || true
