# Awake Mode — Keep LazyClaw working when the lid is closed

**Date:** 2026-05-28
**Status:** Approved → implementing
**Branch:** feat/claude-agent-sdk

## Problem

When the user closes the MacBook lid, macOS sleeps the whole host: Docker pauses,
the Brave bridge dies, heartbeat watchers stop polling, Telegram goes unread. The
user wants the agent to keep working with the lid closed, toggleable by natural
language from Telegram **and** from the Web UI, plus a daily auto-wake and a
"sleep for N hours then come back" command.

## Why a host bridge is mandatory

LazyClaw runs in Docker (Linux). `caffeinate` (holds the macOS no-sleep power
assertion) and `pmset` (schedules a hardware wake) are **macOS-host-only**
binaries — the Linux container physically cannot run them. So control must go
through a small host-side service, exactly like the existing **host Brave bridge**
and **host STT bridge**. The container reaches it via `host.docker.internal`.

### The privilege split

| Action | Command | Needs root? |
|---|---|---|
| Prevent sleep | `caffeinate -dimsu` | no |
| Read status | `pmset -g batt`, `pmset -g sched` | no |
| Schedule wake | `pmset schedule wake …`, `pmset repeat wakeorpoweron …` | **yes** |
| Sleep now | `pmset sleepnow` | **yes** |

Because scheduled wake needs root, this bridge runs as a **root LaunchDaemon**
(`/Library/LaunchDaemons/sh.lazyclaw.awake-bridge.plist`), unlike the Brave/STT
bridges which are user LaunchAgents. Bonus: a root LaunchDaemon starts at **boot,
before login** — exactly what the user's future broken-screen headless-server
MacBook will need. One `sudo` at install time (user pre-approved this trade-off).

### Security model

- Bridge binds `0.0.0.0:18791` (container reaches host via `host.docker.internal`
  → `192.168.65.254` on Docker Desktop; cannot bind localhost-only). Same
  constraint as the STT bridge.
- **Every mutating request requires `Authorization: Bearer <token>`**
  (`LAZYCLAW_HOST_AWAKE_TOKEN`), generated per-install, stored in `.env` + plist.
  Only `GET /health` is unauthenticated.
- A root daemon must **not** exec user-writable files (privilege-escalation
  vector). So the server script + venv are copied to a **root-owned**
  `/usr/local/lazyclaw/host-awake/` tree, not run from the repo (which also sits
  under `~/Desktop`, a TCC-protected path — see `feedback_tcc_denies_desktop_launchd`).
- Standalone server module: no `lazyclaw.*` imports, only `fastapi` + `uvicorn` +
  stdlib `subprocess`. Mirrors `stt_host_server.py`.

## Components

### 1. Host bridge server — `scripts/awake_bridge_server.py`
Standalone FastAPI app, runs as root on the host. Endpoints:

- `GET  /health` — public liveness `{ok, version}`.
- `GET  /awake/status` — `{caffeinate_running, on_ac_power, battery_percent,
  daily_wake, scheduled_wakes}`. Parses `pmset -g batt` + `pmset -g sched`.
- `POST /awake/on  {duration_minutes?: int}` — spawn `caffeinate -dimsu`
  (with `-t <secs>` when a duration is given), store PID in
  `/usr/local/lazyclaw/host-awake/awake.pid`. Idempotent (kills a stale one first).
- `POST /awake/off` — kill the caffeinate process, allow sleep.
- `POST /awake/nap {minutes: int}` — schedule a one-shot `pmset schedule wake` at
  `now + minutes`, kill caffeinate, then (best-effort, after the HTTP response
  flushes) `pmset sleepnow`. The Mac wakes itself at the scheduled time.
- `PUT  /awake/daily-wake {time: "HH:MM", enabled: bool}` — apply or cancel
  `pmset repeat wakeorpoweron <days> HH:MM:SS`.

The bridge is **mechanical only** — it holds no policy. All "should we be awake"
decisions live in the container (settings + heartbeat).

### 2. Installer — `scripts/install-host-awake-bridge.sh` + `make awake-bridge`
Mirrors `install-host-stt-bridge.sh`, adapted for root:
- macOS guard; find host Python 3.11+.
- `sudo` to create root-owned `/usr/local/lazyclaw/host-awake/{venv,awake_bridge_server.py}`;
  `pip install fastapi uvicorn` into the venv.
- Generate/reuse `LAZYCLAW_HOST_AWAKE_TOKEN`; write to `.env` (chmod 600) + plist.
- Write `/Library/LaunchDaemons/sh.lazyclaw.awake-bridge.plist` (root-owned, 644),
  `RunAtLoad` + `KeepAlive`.
- `sudo launchctl bootstrap system <plist>` + `kickstart`.
- Drop marker `data/.host_awake_bridge_installed` (visible in container).
- Verify `GET /health` on `localhost:18791`.
- Companion `scripts/uninstall-host-awake-bridge.sh` (`bootout`, remove plist,
  cancel `pmset repeat`, kill caffeinate, drop marker).
- Makefile: `awake-bridge`, `awake-bridge-uninstall`, `awake-bridge-status`,
  `awake-bridge-restart`.

### 3. Container client — `lazyclaw/host/awake_client.py`
Mirrors the `stt.py` host-bridge client:
- Marker `/app/data/.host_awake_bridge_installed` + `LAZYCLAW_HOST_AWAKE_TOKEN`
  gate usage; host `host.docker.internal:18791` (container) / `127.0.0.1` (native).
- Process-local circuit breaker (`_disabled_until`, 60 s backoff) so a missing
  bridge never adds latency.
- `async` methods: `status()`, `on(duration_minutes=None)`, `off()`,
  `nap(minutes)`, `set_daily_wake(time, enabled)`. Each returns a structured
  dict or raises `AwakeBridgeUnavailable` with an actionable message.

### 4. Settings — `lazyclaw/settings/general.py`
Add an `awake` sub-dict to `DEFAULT_GENERAL`:
```python
"awake": {
    "enabled": True,            # caffeinate held by default when server runs
    "daily_wake_enabled": False,
    "daily_wake_time": "07:00", # HH:MM, user-tz
    "suppressed_until": None,   # ISO ts; set by "nap", respected by reconcile
}
```
Validation in `update_general_settings`: `enabled`/`daily_wake_enabled` → bool;
`daily_wake_time` → `^\d{2}:\d{2}$` with 00–23 / 00–59 range; `suppressed_until`
→ ISO-8601 or null. Stored under `general.awake` (merge, never overwrite siblings).

### 5. NL skill — `lazyclaw/skills/builtin/awake_mode.py`
`BaseSkill` with `config` injection (like `BudgetManagerSkill`). `read_only=False`,
`category="core"`. Auto-discovered by the brain → **Telegram NL + Web-UI chat work
with zero extra wiring**.
```
awake_mode(action: "on"|"off"|"status"|"nap"|"daily_wake",
           duration_minutes?: int,        # for "on"/"nap"
           daily_wake_time?: "HH:MM",      # for "daily_wake"
           daily_wake_enabled?: bool)
```
Each action: update settings (source of truth) → call the host client → return a
human-readable string. **Battery honesty:** if `on` succeeds but `status` shows
the machine is on battery, the reply warns *"awake mode ON, but you're on battery
— closing the lid still sleeps; plug in."* Bridge-missing → clear message telling
the user to run `make awake-bridge`. Registered in `registry.py` next to
`BudgetManagerSkill`.

### 6. Heartbeat reconcile — `lazyclaw/heartbeat/daemon._reconcile_awake_mode()`
Called from `_tick()` (every 60 s, like `_ensure_persistent_browser`). Self-heals
across container restarts and post-wake. Per user with awake settings:
- if `suppressed_until` in the future → skip (in a nap).
- if `enabled` and `status.caffeinate_running` is false → `client.on()`.
- if `daily_wake_enabled` and the live `pmset` repeat schedule drifts from
  `daily_wake_time` → re-apply. Only on drift (no root spam every tick).
- never auto-**off** (disable is an explicit user action via the skill).

Wrapped in `try/except` so a bridge outage never blocks other heartbeat work.

### 7. Gateway route — `lazyclaw/gateway/routes/awake.py`
`GET /api/awake/status` and `POST /api/awake/toggle {enabled}` /
`PUT /api/awake/settings {daily_wake_enabled, daily_wake_time}` — thin wrappers
over the same settings + client used by the skill. Registered in the gateway app
beside the other routers.

### 8. Web UI
- `web/src/api.ts`: `AwakeStatus` type + `getAwakeStatus()` / `setAwakeMode()` /
  `updateAwakeSettings()` (mirrors `getGeneralSettings` pattern).
- `web/src/components/Header.tsx`: a status badge next to the brain badge —
  `🟢 awake` / `🌙 sleep ok`, click toggles on/off, tooltip shows AC/battery +
  next wake.
- `web/src/pages/Settings.tsx`: a "Power" tab — enable toggle, daily-wake enable
  toggle, `HH:MM` time input, all persisted via `updateAwakeSettings`.

### 9. Telegram (polish, optional) — `/awake` command
NL already works via skill auto-discovery. Add a `/awake on|off|status|nap <h>`
command + `BotCommand` entry for one-tap access, calling the same skill.

## Data flow — "sleep for 2 hours"
NL/Web → `awake_mode(action="nap", duration_minutes=120)` → skill writes
`settings.awake.suppressed_until = now+2h` (container DB) → `client.nap(120)` →
bridge schedules one-shot `pmset` wake at now+2h, kills caffeinate, `sleepnow`.
Mac sleeps; container pauses. At +2h the Mac wakes itself → Docker resumes →
heartbeat tick → `suppressed_until` is past → if `enabled`, caffeinate re-asserted
→ quiet Telegram push "back awake."

## Error handling (per coding rules)
- Bridge unreachable / not installed → skill + route return a clear, actionable
  message ("run `make awake-bridge`"); never silent. Circuit breaker prevents
  latency.
- On-battery warning surfaced, never swallowed.
- All host-side `pmset`/`caffeinate` failures logged to
  `~/Library/Logs/LazyClaw/awake-bridge.log` and returned in the HTTP error body.
- Settings validation rejects bad `HH:MM` / non-bool with a clear `ValueError`.

## Testing
Unit (pytest, mockable — no real sleep):
- `tests/test_awake_settings.py` — defaults, `HH:MM` validation (range + format),
  bool coercion, `suppressed_until` ISO/null, merge preserves siblings.
- `tests/test_awake_client.py` — marker/token gating, circuit breaker, host
  resolution, error mapping (mock httpx/urllib).
- `tests/test_awake_pmset_parsers.py` — parse `pmset -g batt` (AC vs battery, %),
  `pmset -g sched` (scheduled wakes), drift comparison.
- `tests/test_awake_skill.py` — action routing, battery-warning text,
  bridge-missing text, settings written before client call.
- `tests/heartbeat/test_reconcile_awake.py` — decision table: suppressed→skip,
  enabled+dead→on, drift→reapply, never auto-off.

Manual (can't unit-test the kernel): real lid-close on AC, daily wake, nap.

## YAGNI — explicitly out
Wake-on-LAN (needs a second device), smart switch (laptops have a battery),
VPS/split architecture (Cloudflare blocks datacenter IPs for Upwork/LinkedIn —
the broken-screen-MacBook server is the long-term plan instead), `weekdays_only`.

## Files
**New:** `scripts/awake_bridge_server.py`, `scripts/install-host-awake-bridge.sh`,
`scripts/uninstall-host-awake-bridge.sh`, `lazyclaw/host/__init__.py`,
`lazyclaw/host/awake_client.py`, `lazyclaw/skills/builtin/awake_mode.py`,
`lazyclaw/gateway/routes/awake.py`, `web/src/components/AwakeBadge.tsx`,
5 test files.
**Edited:** `lazyclaw/settings/general.py`, `lazyclaw/skills/registry.py`,
`lazyclaw/heartbeat/daemon.py`, `lazyclaw/gateway/app.py`, `Makefile`,
`web/src/api.ts`, `web/src/components/Header.tsx`, `web/src/pages/Settings.tsx`,
`lazyclaw/channels/telegram_commands.py`, `CLAUDE.md`, `DOCS.md`, `TODO.md`,
`MEMORY.md`.
