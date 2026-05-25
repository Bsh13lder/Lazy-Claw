#!/usr/bin/env bash
# Host-side watcher for the LazyClaw Brave bridge — self-healing + HTTP heal API.
#
# Single source of truth: ~/.lazyclaw/bridge.env
#
# Two responsibilities:
#   A. POLL LOOP — every 30s, curl localhost:${BRAVE_CDP_PORT}/json/version.
#      - If it responds, sleep.
#      - If not, log and `launchctl kickstart -k gui/$UID/<bridge-label>`,
#        wait 8s, recheck. After 3 consecutive kickstarts that don't bring
#        the port up, back off to one attempt every 5 minutes (and log it
#        loudly so the user knows their regular Brave is blocking).
#      - We probe the PORT, not the process — a Brave that ignored the
#        --remote-debugging-port arg (e.g. user's manually-opened one
#        holding the SingletonLock) is treated as a failure mode.
#
#   B. HEAL HTTP ENDPOINT — listens on 127.0.0.1:${HEAL_ENDPOINT_PORT} (NOT
#      0.0.0.0; we don't expose this off-host). POST /heal triggers an
#      immediate kickstart, waits ~8s, returns JSON:
#        {"status":"healed","port_listening":true|false,"final_check_at":"..."}
#      Wired up so the container can ping `host.docker.internal:9224/heal`
#      when CDP looks dead instead of failing the task.
#
# Run via the launchd plist installed by install-host-brave-bridge.sh.
# `KeepAlive: true` revives this script if it ever exits.

set -uo pipefail

BRIDGE_ENV_FILE="${BRIDGE_ENV_FILE:-$HOME/.lazyclaw/bridge.env}"
if [[ ! -f "$BRIDGE_ENV_FILE" ]]; then
    echo "[$(date -u +%FT%TZ)] FATAL: $BRIDGE_ENV_FILE missing — run scripts/install-host-brave-bridge.sh" >&2
    exit 2
fi
# shellcheck disable=SC1090
source "$BRIDGE_ENV_FILE"

: "${BRAVE_CDP_PORT:=9222}"
: "${HEAL_ENDPOINT_PORT:=9224}"

PLIST_LABEL="sh.lazyclaw.brave-bridge"
DOMAIN="gui/$(id -u)"
KICKSTART_TARGET="${DOMAIN}/${PLIST_LABEL}"
LOG_FILE="${LOG_FILE:-$HOME/Library/Logs/LazyClaw/brave-bridge-watcher.log}"
mkdir -p "$(dirname "$LOG_FILE")"

stamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log()   { echo "[$(stamp)] $*" | tee -a "$LOG_FILE" >&2; }

probe_cdp() {
    curl -sf -m 3 "http://127.0.0.1:${BRAVE_CDP_PORT}/json/version" >/dev/null 2>&1
}

kickstart_bridge() {
    launchctl kickstart -k "$KICKSTART_TARGET" >/dev/null 2>&1
}

# Returns 0 if port is listening within the timeout, else 1.
wait_for_port() {
    local timeout="${1:-10}"
    for _ in $(seq 1 "$timeout"); do
        probe_cdp && return 0
        sleep 1
    done
    return 1
}

# Brave-running check used only for clearer logging (port probe is authoritative).
brave_main_running() {
    pgrep -f '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser$' >/dev/null 2>&1
}

# ── State for backoff ──────────────────────────────────────────────────

CONSECUTIVE_FAIL=0
MAX_FAST_FAILS=2       # one kickstart, one kill+kickstart, then backoff
BACKOFF_SECONDS=300    # 5 min

# Detects a Brave main process that is NOT running with --remote-debugging-port.
# Such a Brave is the "blocker" — it owns the profile SingletonLock so any
# bridge kickstart hands off via single-instance and just spawns a new window
# without ever opening the CDP port. Returns 0 if blocker detected.
brave_blocker_running() {
    # List Brave main-process PIDs (not Helpers), check each for the flag.
    local pids
    pids=$(pgrep -f '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser' 2>/dev/null \
           | xargs -I{} bash -c 'ps -p {} -o command= 2>/dev/null | grep -v Helper' \
           | xargs -n1 echo 2>/dev/null || true)
    pgrep -f '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser$' >/dev/null 2>&1 || return 1
    # If ANY Brave main has --remote-debugging-port, bridge is alive — not a blocker.
    if pgrep -f -- '--remote-debugging-port=' >/dev/null 2>&1; then
        return 1
    fi
    # Brave is running but NONE with the CDP flag → it's the blocker.
    return 0
}

# Gentle then forceful kill of the blocker. SIGTERM, wait up to 6s for graceful
# exit, then SIGKILL anything still alive. Only kills the main browser process
# (not Helpers — they exit when the parent does).
kill_blocker() {
    log "  ⚠ killing blocker Brave (no --remote-debugging-port flag)"
    pkill -TERM -f '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser$' 2>/dev/null || true
    local i
    for i in 1 2 3 4 5 6; do
        sleep 1
        pgrep -f '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser$' >/dev/null 2>&1 || return 0
    done
    log "  ⚠ blocker did not exit on SIGTERM after 6s — SIGKILL"
    pkill -KILL -f '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser$' 2>/dev/null || true
    sleep 2
}

attempt_heal() {
    # Self-pacing heal:
    #   pass 1 → blind kickstart. Works on cold-start (no Brave running).
    #   pass 2 → if Brave is running without CDP flag, kill the blocker, then
    #            kickstart. This prevents the "spawn window every 30s" loop
    #            we hit on 2026-05-21 when user opened Brave manually.
    # After 2 failed passes → backoff (handled by caller via MAX_FAST_FAILS).
    log "heal attempt $((CONSECUTIVE_FAIL + 1))/$MAX_FAST_FAILS for $KICKSTART_TARGET"

    # Pass 2+ logic: if a blocker is around, kill it BEFORE kickstart so the
    # new launch isn't swallowed by single-instance handoff.
    if [[ $CONSECUTIVE_FAIL -ge 1 ]] && brave_blocker_running; then
        kill_blocker
    fi

    kickstart_bridge
    if wait_for_port 10; then
        log "  → CDP up on :${BRAVE_CDP_PORT}"
        CONSECUTIVE_FAIL=0
        return 0
    fi

    CONSECUTIVE_FAIL=$((CONSECUTIVE_FAIL + 1))
    if brave_blocker_running; then
        log "  ⚠ CDP still down — Brave blocker still holding the profile lock at: ${BRAVE_PROFILE_DIR:-<unset>}. Next attempt will kill it."
    elif brave_main_running; then
        log "  ⚠ CDP still down — Brave process IS running with some flag set. Not killing to avoid stomping on an active bridge variant."
    else
        log "  ⚠ CDP still down — Brave process not running. launchd kickstart may be failing — check ~/Library/Logs/LazyClaw/brave-bridge.err"
    fi
    return 1
}

# ── HTTP heal endpoint (background, owned by this script) ──────────────

start_heal_endpoint() {
    # Minimal HTTP server in Python — single-threaded, blocks on requests.
    # Triggers a kickstart + waits ~10s + returns JSON status. Listens on
    # 127.0.0.1 ONLY; we don't expose this off-host.
    HEAL_PORT="$HEAL_ENDPOINT_PORT" \
    BRAVE_CDP_PORT_FOR_HEAL="$BRAVE_CDP_PORT" \
    KICKSTART_TARGET="$KICKSTART_TARGET" \
    HEAL_LOG_FILE="$LOG_FILE" \
    python3 - <<'PYEOF' &
import http.server
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

PORT = int(os.environ["HEAL_PORT"])
CDP_PORT = int(os.environ["BRAVE_CDP_PORT_FOR_HEAL"])
TARGET = os.environ["KICKSTART_TARGET"]
LOG_PATH = os.environ.get("HEAL_LOG_FILE", "")

def _log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] heal-api: {msg}\n"
    if LOG_PATH:
        try:
            with open(LOG_PATH, "a") as f:
                f.write(line)
        except Exception:
            pass
    sys.stderr.write(line)

def _probe():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=3) as r:
            return 200 <= r.status < 300
    except Exception:
        return False

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access log
        pass

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/heal":
            self._send_json(404, {"error": "not found", "path": self.path})
            return
        _log("POST /heal — kickstart")
        try:
            subprocess.run(["launchctl", "kickstart", "-k", TARGET],
                           check=False, capture_output=True, timeout=5)
        except Exception as e:
            _log(f"kickstart failed: {e}")
        # Wait up to 10s for port to come up.
        deadline = time.time() + 10
        listening = False
        while time.time() < deadline:
            if _probe():
                listening = True
                break
            time.sleep(1)
        payload = {
            "status": "healed",
            "port_listening": listening,
            "final_check_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cdp_port": CDP_PORT,
        }
        self._send_json(200, payload)

    def do_GET(self):
        if self.path in ("/health", "/heal"):
            self._send_json(200, {
                "port_listening": _probe(),
                "cdp_port": CDP_PORT,
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            return
        self._send_json(404, {"error": "not found", "path": self.path})

try:
    # Bind explicitly to 127.0.0.1 — NEVER 0.0.0.0.
    srv = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
except OSError as e:
    _log(f"could not bind 127.0.0.1:{PORT} — {e}")
    sys.exit(0)  # let launchd revive the watcher; another instance may own the port

_log(f"heal endpoint listening on 127.0.0.1:{PORT} (cdp_port={CDP_PORT})")
try:
    srv.serve_forever()
except KeyboardInterrupt:
    pass
PYEOF
    HEAL_PID=$!
    log "heal endpoint child pid=$HEAL_PID (127.0.0.1:${HEAL_ENDPOINT_PORT})"
}

cleanup() {
    if [[ -n "${HEAL_PID:-}" ]]; then
        kill "$HEAL_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# ── Main loop ──────────────────────────────────────────────────────────

log "host-bridge-watcher started (cdp_port=${BRAVE_CDP_PORT}, heal_port=${HEAL_ENDPOINT_PORT}, profile=${BRAVE_PROFILE_DIR:-<unset>})"

start_heal_endpoint

while true; do
    if probe_cdp; then
        if [[ $CONSECUTIVE_FAIL -ne 0 ]]; then
            log "CDP back up on :${BRAVE_CDP_PORT} — clearing fail streak"
            CONSECUTIVE_FAIL=0
        fi
        sleep 30
        continue
    fi

    # CDP down. Try to heal.
    if attempt_heal; then
        sleep 30
        continue
    fi

    # Still down after kickstart.
    if [[ $CONSECUTIVE_FAIL -ge $MAX_FAST_FAILS ]]; then
        log "🛑 ${CONSECUTIVE_FAIL} consecutive failed kickstarts — backing off ${BACKOFF_SECONDS}s. Likely cause: user's regular Brave is holding the profile dir lock. Quit Brave once (Cmd+Q) to let the bridge take over."
        sleep "$BACKOFF_SECONDS"
        # After backoff, reset streak so we try fast-kick again.
        CONSECUTIVE_FAIL=0
        continue
    fi

    sleep 30
done
